"""Observer: extracts structured observations from tool interactions.

Based on Generative Agents (Park et al., 2023) importance scoring and
CoALA (Sumers et al., 2023) memory type taxonomy.
"""
import json
from lib import llm, db, logger, config

_log = logger.get()

EXTRACTION_SYSTEM = """你是一个严格的记忆观察器。你的任务是从工具交互中提取真正有价值的结构化观察。

返回 JSON 对象，字段：
- "should_save": 布尔值
- "quality": 1-5 整数（5=极其重要，1=勉强值得）
- "importance": 1-10 整数（10=改变项目方向，1=琐碎细节）
- "category": "observation", "user", "reference", "feedback", "project"
- "obs_type": "discovery", "bugfix", "change", "decision", "feature", "refactor", "security_note", null
- "memory_type": "episodic", "semantic", "procedural", "reflective"
- "title": 简明标题（60字以内）
- "narrative": 1-3 句话总结
- "facts": 字符串数组，具体事实
- "concepts": 字符串数组，标签
- "module": 该记忆所属的任务模块名（英文 kebab-case，如 "auth-system", "db-migration", "api-endpoints"）。优先复用已有模块，仅当与所有现有模块差异明显时才创建新模块。
- "related_memory_ids": 与该记忆最相关的已有记忆 ID 数组（1-5个）。基于因果关系、同一功能、同一问题判断。如果是全新话题，可以为空数组。

memory_type 说明：
- "episodic": 具体发生了什么事（bug修复过程、功能开发过程）
- "semantic": 提炼的知识和事实（用户偏好、架构决策、配置信息）
- "procedural": 可复用的操作步骤（如何部署、如何测试、解决模板）
- "reflective": 从经验中提取的教训（踩过的坑、最佳实践）

严格规则 - 只保存真正有价值的信息：
应该保存（quality>=4, importance>=5, should_save=true）：
- 用户明确表达的偏好、决策、需求
- 发现并修复了 bug（有具体的错误信息和修复方案）
- 重要的架构决策或技术选型
- 完成了有意义的功能开发或重构
- 安全相关的发现
- 踩过的坑和经验教训（具体可复现的）

绝对不保存（should_save=false）：
- 普通文件读取，即使读了配置文件
- 常规搜索操作（Grep/Glob），无论是否找到结果
- 浏览器截图、页面快照等测试操作
- favicon.ico 404 之类的琐碎问题
- 重复或相似的操作
- 临时调试信息
- 纯粹的信息查询

关键原则：宁可漏掉，不要噪音。一条高质量记忆胜过十条垃圾。
只输出有效 JSON。"""

BATCH_SYSTEM = """你是一个严格的记忆观察器。分析以下工具交互，只提取真正有价值的观察。

返回 JSON 数组，每个元素包含：
- "quality": 1-5（必须>=4才值得保存）
- "importance": 1-10（10=极其重要）
- "category": "observation", "user", "reference", "feedback", "project"
- "obs_type": "discovery", "bugfix", "change", "decision", "feature", "refactor", "security_note", null
- "memory_type": "episodic", "semantic", "procedural", "reflective"
- "title": 简明标题（60字以内）
- "narrative": 1-3 句话总结
- "facts": 字符串数组
- "concepts": 字符串数组
- "module": 任务模块名（英文 kebab-case）。优先复用已有模块。
- "related_memory_ids": 相关记忆 ID 数组（1-5个）

memory_type 说明：
- "episodic": 具体发生了什么事
- "semantic": 提炼的知识和事实
- "procedural": 可复用的操作步骤
- "reflective": 从经验中提取的教训

严格标准：
值得保存（quality>=4, importance>=5）：
- 用户需求/偏好/决策（明确表达的）
- Bug修复（有具体错误和方案）
- 功能开发（代码实际变更）
- 架构决策
- 可复现的经验教训

不保存（跳过）：
- 普通文件读取/搜索
- 浏览器测试操作
- 琐碎错误（favicon 404等）
- 重复操作
- 临时调试

目标：从这组交互中提取 0-3 条高质量记忆。宁缺毋滥。
只输出 JSON 数组。"""

REFLECTION_SYSTEM = """你是一个会话反思器。分析这次编程会话的完整过程，提取高层次的经验教训。

返回 JSON 对象：
- "reflection_title": 反思标题
- "narrative": 2-4 句话总结这次会话的核心收获
- "lessons": 字符串数组，具体的经验教训
- "patterns": 字符串数组，发现的模式或最佳实践
- "importance": 1-10 重要性评分
- "facts": 字符串数组，关键事实

重点提取：
- 做了什么决定，为什么
- 遇到了什么问题，怎么解决的
- 有什么可以复用的经验
- 有什么需要避免的陷阱

只输出有效 JSON。"""

SKILL_EXTRACTION_SYSTEM = """你是一个 Skill 提取器。分析这次编程会话的完整过程，判断是否值得从中提取一个可复用的 Skill。

Skill 是给 Agent 使用的可复用工作能力包：把一类任务的触发条件、操作流程、参考资料和验收标准放在一起，让 Agent 遇到同类问题时可以按既定方法做。

返回 JSON 对象：
- "should_extract": 布尔值，是否值得提取 Skill
- "skill_name": Skill 名称（简短，英文 kebab-case）
- "description": 触发条件描述（用户说什么时应该触发这个 Skill）
- "workflow": 字符串数组，工作流程步骤（每步一个字符串，写具体动作而非原则）
- "trigger_keywords": 字符串数组，触发关键词
- "stop_conditions": 字符串数组，停止条件（什么情况下要停下来确认）
- "output_format": 字符串，输出交付物描述
- "examples": 字符串数组，这次会话中的具体案例（可作为 examples/ 内容）
- "gotchas": 字符串数组，踩过的坑和注意事项
- "references": 字符串数组，涉及的文件、工具、API 等参考资源
- "confidence": 1-5，对这个 Skill 质量的自信程度

严格标准 - 只提取真正值得复用的 Skill：
应该提取（should_extract=true）：
- 任务重复出现，且每次都需要类似流程
- 任务容易跑偏，需要明确的步骤约束
- 涉及多个工具的固定组合
- 有明确的输入/输出格式
- 这次会话中反复纠正过 Agent 的行为

不提取（should_extract=false）：
- 一次性的简单任务
- 流程完全取决于具体上下文，无法泛化
- 任务太简单，写 Skill 的成本大于收益
- 会话中没有形成稳定的流程

关键原则：Skill 的价值在于"把反复纠正过的做法留下来"。如果这次会话没有反复纠正、没有踩坑、没有形成稳定流程，就不要提取。
只输出有效 JSON。"""

REASONING_EXTRACTION_SYSTEM = """你是一个推理过程分析器。分析用户的问题和AI的回复，提取结构化的推理链。

返回 JSON 对象：
- "question": 用户的核心问题（简洁概括）
- "module": 此推理属于哪个模块（用短横线命名，如 voice-separation、bug-fix），如果没有明确模块则留空字符串
- "steps": 推理步骤数组，每步包含：
  - "thought": 这步在想什么
  - "action": 做了什么（如果有）
  - "observation": 观察到什么（如果有）
- "outcome": "success" / "failure" / "partial"
- "outcome_summary": 结果总结（1-2句话）
- "failure_reason": 如果失败，原因是什么
- "extracted_facts": 从推理中提取的可复用事实/知识数组
- "importance": 1-10 重要性评分

只输出有效 JSON。"""


EXTRACTION_PROMPT_TEMPLATE = """项目: {project}
工具: {tool_name}
工具输入（截断）: {tool_input}
工具响应（截断）: {tool_response}
最近上下文: {context}

已有模块列表（优先复用，仅当差异明显时才创建新模块）：
{modules_list}

从这个交互中提取结构化观察。"""


def _truncate(text, max_len=2000):
    if not text:
        return ""
    text = str(text)
    return text[:max_len] + "...[truncated]" if len(text) > max_len else text


def extract_from_interaction(project, tool_name, tool_input, tool_response, context=""):
    """Use LLM to extract structured observation from a tool interaction.

    Raises exception on LLM/network failures (for retry).
    Returns None only when LLM responds but observation is not worth saving.
    """
    client = llm.get()
    # Build modules list for the prompt
    modules = db.get_modules(project)
    modules_list = "\n".join(f"- {m['name']}: {m['description'] or '无描述'} ({m['memory_count']}条)" for m in modules) if modules else "（暂无模块）"

    prompt = EXTRACTION_PROMPT_TEMPLATE.format(
        project=project,
        tool_name=tool_name,
        tool_input=_truncate(tool_input, 1500),
        tool_response=_truncate(tool_response, 1500),
        context=_truncate(context, 500),
        modules_list=modules_list,
    )
    raw = client.chat(
        messages=[{"role": "user", "content": prompt}],
        system=EXTRACTION_SYSTEM,
        temperature=0.2,
    )
    data = client.extract_json(raw)
    if not data or not isinstance(data, dict):
        _log.warning(f"Observer returned non-JSON: {raw[:200]}")
        return None
    return data


def extract_batch(project, interactions, context=""):
    """Batch extract observations from multiple interactions at once."""
    client = llm.get()
    summary_lines = []
    for i, item in enumerate(interactions):
        tool = item.get("tool_name", item.get("hook_event", "?"))
        inp = ""
        try:
            raw_inp = item.get("tool_input", "")
            if isinstance(raw_inp, str):
                raw_inp = json.loads(raw_inp) if raw_inp else {}
            if isinstance(raw_inp, dict):
                if tool in ("Read", "Edit", "Write"):
                    inp = raw_inp.get("file_path", "")
                elif tool == "Bash":
                    inp = str(raw_inp.get("command", ""))[:100]
                elif tool in ("Grep", "Glob"):
                    inp = raw_inp.get("pattern", "")
                else:
                    inp = str(raw_inp)[:80]
        except:
            inp = str(item.get("tool_input", ""))[:80]
        resp_preview = str(item.get("tool_response", ""))[:200]
        summary_lines.append(f"{i+1}. [{tool}] {inp} -> {resp_preview}")

    prompt = f"""项目: {project}
交互记录（共 {len(interactions)} 条）:
{chr(10).join(summary_lines[:30])}

上下文: {context}

已有模块列表（优先复用，仅当差异明显时才创建新模块）：
{chr(10).join(f'- {m["name"]}: {m["description"] or "无描述"} ({m["memory_count"]}条)' for m in db.get_modules(project)) or '（暂无模块）'}

从这些交互中提取所有值得记忆的结构化观察。"""

    raw = client.chat(
        messages=[{"role": "user", "content": prompt}],
        system=BATCH_SYSTEM,
        temperature=0.2,
    )
    data = client.extract_json(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "observations" in data:
        return data["observations"]
    _log.warning(f"Batch observer returned unexpected format: {str(data)[:200]}")
    return []


def generate_reflection(project, session_uuid, interactions):
    """Generate a session reflection (reflective memory) from accumulated interactions."""
    if not interactions:
        return None
    client = llm.get()
    summary_lines = []
    for item in interactions[-30:]:
        tool = item.get("tool_name", item.get("hook_event", "?"))
        inp = ""
        try:
            raw_inp = item.get("tool_input", "")
            if isinstance(raw_inp, str):
                raw_inp = json.loads(raw_inp) if raw_inp else {}
            if isinstance(raw_inp, dict):
                if tool in ("Read", "Edit", "Write"):
                    inp = raw_inp.get("file_path", "")
                elif tool == "Bash":
                    inp = str(raw_inp.get("command", ""))[:80]
                else:
                    inp = str(raw_inp)[:60]
        except:
            inp = str(item.get("tool_input", ""))[:60]
        summary_lines.append(f"[{tool}] {inp}")

    prompt = f"""项目: {project}
会话交互记录（共 {len(interactions)} 条）:
{chr(10).join(summary_lines)}

分析这次会话，提取高层次的经验教训。"""

    raw = client.chat(
        messages=[{"role": "user", "content": prompt}],
        system=REFLECTION_SYSTEM,
        temperature=0.3,
    )
    data = client.extract_json(raw)
    if data and isinstance(data, dict) and data.get("lessons"):
        return data
    return None


def extract_skill(project, session_uuid, interactions):
    """Extract a reusable Skill from the session interactions.

    Returns a skill dict if worth extracting, None otherwise.
    """
    if not interactions or len(interactions) < 3:
        return None
    client = llm.get()
    summary_lines = []
    for item in interactions[-40:]:
        tool = item.get("tool_name", item.get("hook_event", "?"))
        inp = ""
        try:
            raw_inp = item.get("tool_input", "")
            if isinstance(raw_inp, str):
                raw_inp = json.loads(raw_inp) if raw_inp else {}
            if isinstance(raw_inp, dict):
                if tool in ("Read", "Edit", "Write"):
                    inp = raw_inp.get("file_path", "")
                elif tool == "Bash":
                    inp = str(raw_inp.get("command", ""))[:80]
                else:
                    inp = str(raw_inp)[:60]
        except:
            inp = str(item.get("tool_input", ""))[:60]
        summary_lines.append(f"[{tool}] {inp}")

    prompt = f"""项目: {project}
会话交互记录（共 {len(interactions)} 条）:
{chr(10).join(summary_lines)}

分析这次会话，判断是否值得从中提取一个可复用的 Skill。
如果值得，描述 Skill 的完整结构。"""

    try:
        raw = client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=SKILL_EXTRACTION_SYSTEM,
            temperature=0.3,
        )
        data = client.extract_json(raw)
        if data and isinstance(data, dict) and data.get("should_extract"):
            return data
    except Exception as e:
        _log.error(f"Skill extraction failed: {e}")
    return None


def process_skill_extraction(project, session_uuid, interactions):
    """Extract and save a Skill from the session."""
    skill = extract_skill(project, session_uuid, interactions)
    if not skill:
        return None
    skill_id = db.insert_skill(
        project=project,
        name=skill.get("skill_name", "unnamed"),
        description=skill.get("description", ""),
        workflow=skill.get("workflow", []),
        trigger_keywords=skill.get("trigger_keywords", []),
        stop_conditions=skill.get("stop_conditions", []),
        output_format=skill.get("output_format", ""),
        examples=skill.get("examples", []),
        gotchas=skill.get("gotchas", []),
        references=skill.get("references", []),
        confidence=skill.get("confidence", 3),
        origin_session=session_uuid,
    )
    _log.info(f"Skill extracted: id={skill_id} name={skill.get('skill_name')} confidence={skill.get('confidence')}")
    return skill_id


def process_interaction(project, tool_name, tool_input, tool_response, context="", session_uuid=None, session_phase=None):
    """Extract observation and save to DB if worthwhile."""
    data = extract_from_interaction(project, tool_name, tool_input, tool_response, context)
    if not data or not data.get("should_save"):
        return None
    quality = data.get("quality", 3)
    importance = data.get("importance", 5)
    if quality < 4 or importance < 5:
        _log.debug(f"Skipping low quality observation (q={quality}, i={importance}): {data.get('title', '')[:40]}")
        return None
    return _save_observation(project, data, session_uuid, session_phase)


def process_batch(project, interactions, context="", session_uuid=None):
    """Batch process multiple interactions and save all extracted observations."""
    observations = extract_batch(project, interactions, context)
    saved = 0
    for obs in observations:
        if not obs or not isinstance(obs, dict):
            continue
        quality = obs.get("quality", 3)
        importance = obs.get("importance", 5)
        if quality < 4 or importance < 5:
            _log.debug(f"Skipping low quality batch observation (q={quality}, i={importance}): {obs.get('title', '')[:40]}")
            continue
        mem_id = _save_observation(project, obs, session_uuid)
        if mem_id:
            saved += 1
    return saved


def process_reflection(project, session_uuid, interactions):
    """Generate and save a reflective memory from session."""
    reflection = generate_reflection(project, session_uuid, interactions)
    if not reflection:
        return None
    data = {
        "should_save": True,
        "quality": 4,
        "importance": reflection.get("importance", 6),
        "category": "observation",
        "obs_type": "decision",
        "memory_type": "reflective",
        "title": reflection.get("reflection_title", "Session Reflection"),
        "narrative": reflection.get("narrative", ""),
        "facts": reflection.get("lessons", []) + reflection.get("patterns", []),
        "concepts": ["反思", "经验教训"] + reflection.get("patterns", []),
    }
    return _save_observation(project, data, session_uuid)


def _save_observation(project, data, session_uuid=None, session_phase=None):
    """Save a single observation dict to the database, with module assignment and linking."""
    category = data.get("category", "observation")
    obs_type = data.get("obs_type")
    memory_type = data.get("memory_type", "episodic")
    facts = data.get("facts", [])
    concepts = data.get("concepts", [])
    quality = data.get("quality", 4)
    importance = data.get("importance", 5)

    valid_categories = ("observation", "user", "reference", "feedback", "project")
    if category not in valid_categories:
        category = "observation"
    valid_types = ("episodic", "semantic", "procedural", "reflective")
    if memory_type not in valid_types:
        memory_type = "episodic"

    metadata = json.dumps({"quality": quality}, ensure_ascii=False)

    # Module assignment
    module_name = data.get("module")
    module_id = None
    if module_name:
        # Normalize module name
        module_name = module_name.strip().lower().replace(" ", "-").replace("_", "-")
        module_desc = data.get("module_description", "")
        module_id = db.get_or_create_module(project, module_name, module_desc)

    # Related memory IDs
    related_ids = data.get("related_memory_ids", [])
    if related_ids:
        # Validate that referenced memories exist
        related_ids = [int(rid) for rid in related_ids if isinstance(rid, (int, str)) and str(rid).isdigit()]
        related_ids = related_ids[:5]  # Cap at 5

    mem_id = db.insert_memory(
        project=project,
        category=category,
        obs_type=obs_type,
        memory_type=memory_type,
        title=data.get("title"),
        subtitle=data.get("subtitle"),
        narrative=data.get("narrative"),
        facts=facts,
        concepts=concepts,
        name=data.get("name"),
        description=data.get("description"),
        content=data.get("narrative") or data.get("title"),
        origin_session=session_uuid,
        generated_by=config_model_name(),
        importance=importance,
        session_phase=session_phase,
        metadata=metadata,
        module_id=module_id,
        related_to=related_ids,
    )

    # Update module count
    if module_id:
        db.increment_module_count(module_id)

    # Add reverse links to referenced memories
    if related_ids:
        _add_reverse_links(mem_id, related_ids)

    _log.info(f"Observation saved: id={mem_id} q={quality} i={importance} cat={category} type={obs_type} mtype={memory_type} module={module_name} title={data.get('title', '')[:40]}")
    return mem_id


def _add_reverse_links(source_id, target_ids):
    """Add reverse links: for each target memory, add source_id to its related_to list."""
    for target_id in target_ids:
        try:
            target = db.get_memory(target_id)
            if target:
                existing = json.loads(target["related_to"] or "[]")
                if source_id not in existing and len(existing) < 10:
                    existing.append(source_id)
                    db.update_memory(target_id, related_to=json.dumps(existing, ensure_ascii=False))
        except Exception as e:
            _log.debug(f"Reverse link failed for #{target_id}: {e}")


def summarize_session(project, session_uuid, interactions):
    """Generate a session summary from accumulated interactions."""
    if not interactions:
        return None
    client = llm.get()
    summary_prompt = f"""总结项目 "{project}" 的这次编程会话。

交互记录:
{json.dumps(interactions[-20:], ensure_ascii=False, indent=1)}

返回 JSON 对象:
- "request": 用户试图做什么
- "investigated": 检查/探索了什么
- "learned": 关键发现
- "completed": 完成了什么
- "next_steps": 建议的下一步操作
- "notes": 重要观察

只输出有效 JSON。"""

    try:
        raw = client.chat(
            messages=[{"role": "user", "content": summary_prompt}],
            system="你是会话总结器。简洁具体。只输出 JSON。",
            temperature=0.2,
        )
        data = client.extract_json(raw)
        if data:
            db.upsert_session(session_uuid, project, data.get("request"))
            db.complete_session(session_uuid, json.dumps(data, ensure_ascii=False))
            return data
    except Exception as e:
        _log.error(f"Session summary failed: {e}")
    return None


def config_model_name():
    return config.get("llm", "model") or "unknown"


# ── Reasoning Chain Extraction ──────────────────────────────────

def extract_reasoning_chain(project, user_message, ai_response, session_uuid=None):
    """Extract a structured reasoning chain from a user question and AI response.

    Returns the chain dict if worth saving, None otherwise.
    Raises on LLM/network failures (for retry).
    """
    client = llm.get()
    prompt = f"""项目: {project}

用户问题: {user_message[:1500]}

AI回复（截断）: {ai_response[:3000]}

分析这段交互的推理过程。"""

    raw = client.chat(
        messages=[{"role": "user", "content": prompt}],
        system=REASONING_EXTRACTION_SYSTEM,
        temperature=0.2,
    )
    data = client.extract_json(raw)
    if not data:
        return None

    outcome = data.get("outcome", "pending")
    importance = data.get("importance", 5)
    question = data.get("question", user_message[:200])
    steps = data.get("steps", [])

    # Only save chains with meaningful content
    if not steps and outcome == "pending":
        return None

    # Determine module for this reasoning chain
    module_name = data.get("module", "")
    module_id = None
    if module_name and module_name.strip():
        module_name = module_name.strip().lower().replace(" ", "-").replace("_", "-")
        module_id = db.get_or_create_module(project, module_name, "")

    chain_id = db.insert_reasoning_chain(
        project=project,
        module_id=module_id,
        question=question,
        steps=steps,
        outcome=outcome,
        outcome_summary=data.get("outcome_summary"),
        failure_reason=data.get("failure_reason"),
        extracted_facts=data.get("extracted_facts", []),
        session_uuid=session_uuid,
        importance=importance,
    )

    # Also save extracted facts as regular memories if important enough
    facts = data.get("extracted_facts", [])
    if facts and importance >= 6:
        db.insert_memory(
            project=project,
            category="observation",
            obs_type="discovery",
            memory_type="semantic",
            title=f"推理提取: {question[:60]}",
            narrative=f"从推理中提取的知识",
            facts=facts,
            origin_session=session_uuid,
            generated_by=config_model_name(),
            importance=importance,
        )

    _log.info(f"Reasoning chain extracted: id={chain_id} outcome={outcome} steps={len(steps)}")
    return {"id": chain_id, "outcome": outcome, "question": question}
