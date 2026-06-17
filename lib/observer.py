"""Observer: extracts structured observations from tool interactions.

Based on Generative Agents (Park et al., 2023) importance scoring and
CoALA (Sumers et al., 2023) memory type taxonomy.
"""
import json
from lib import llm, db, logger, config

_log = logger.get()

EXTRACTION_SYSTEM = """你是一个严格的记忆观察器。你的任务是从工具交互中提取真正有价值的结构化观察。

核心原则：只保存能帮助未来解决类似问题的知识。如果这条记忆在未来没有实际用途，就不要保存。

返回 JSON 对象，字段：
- "should_save": 布尔值
- "quality": 1-5 整数（5=极其重要，1=勉强值得）
- "importance": 1-10 整数（10=改变项目方向，1=琐碎细节）
- "category": "observation", "user", "reference", "feedback", "project"
- "obs_type": "discovery", "bugfix", "change", "decision", "feature", "refactor", "security_note", null
- "memory_type": "episodic", "semantic", "procedural", "reflective"
- "title": 简明标题（60字以内），必须体现具体问题或方案
- "narrative": 2-3 句话，必须包含：遇到了什么问题 + 如何解决的 + 关键教训
- "facts": 字符串数组，每条事实必须是可执行的知识（如"应该用X而不是Y"、"配置Z需要设置W"）
- "concepts": 字符串数组，标签
- "module": 该记忆所属的任务模块名（英文 kebab-case）。优先复用已有模块。
- "related_memory_ids": 与该记忆最相关的已有记忆 ID 数组（1-5个）。

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
- 只是执行命令但没有产生新知识

narrative 质量标准：
- 差："从推理中提取的知识"（太笼统，无信息量）
- 好："训练中 SI-SDR 损失导致 mask 塌缩，原因是 scale-invariant 特性在相位不匹配时产生优化退化，修复方案是添加 Waveform L1 损失"
- 差："用户查询了项目信息"（没有知识价值）
- 好："用户决定使用方案 B 而非方案 A，因为 B 方案删除了 DSP 理论，有完整的 2024-SOTA 实验数据支撑"

关键原则：宁可漏掉，不要噪音。一条高质量记忆胜过十条垃圾。
只输出有效 JSON。"""

BATCH_SYSTEM = """你是一个严格的记忆观察器。分析以下工具交互，只提取真正有价值的观察。

核心原则：只保存能帮助未来解决类似问题的知识。

返回 JSON 数组，每个元素包含：
- "quality": 1-5（必须>=4才值得保存）
- "importance": 1-10（10=极其重要）
- "category": "observation", "user", "reference", "feedback", "project"
- "obs_type": "discovery", "bugfix", "change", "decision", "feature", "refactor", "security_note", null
- "memory_type": "episodic", "semantic", "procedural", "reflective"
- "title": 简明标题（60字以内），必须体现具体问题或方案
- "narrative": 2-3 句话，必须包含：遇到了什么问题 + 如何解决的 + 关键教训
- "facts": 字符串数组，每条事实必须是可执行的知识
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
- 只是执行命令但没有产生新知识

narrative 质量标准：
- 差："从推理中提取的知识"（太笼统，无信息量）
- 好："训练中 SI-SDR 损失导致 mask 塌缩，修复方案是添加 Waveform L1 损失"

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

评估标准：
1. 只提取有实际解决方案或明确结论的推理。跳过纯闲聊、简单问答、或没有实质推理过程的交互。
2. 如果AI没有给出有效的解决方案（如只说了"我不知道"、"无法解决"、或明显回避问题），标记 outcome="failure" 并在 failure_reason 中说明。
3. 如果交互中没有可复用的推理过程（如只是执行命令、读取文件），返回空 steps 数组。

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
- "skip": true 如果这个交互不值得保存推理链（如纯命令执行、闲聊、无实质推理）

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


# ── Solution-Oriented Memory Extraction ─────────────────────────

SOLUTION_EXTRACTION_SYSTEM = """你是一个严格的问题解决追踪器。分析完整的会话记录，只提取经过实际验证、确实有效的解决方案。

核心原则：
1. 只提取经过"实际运行/测试验证"后确认有效的方案
2. 推理和假设不算验证——必须看到实际结果
3. 试错几次就找到的不算——必须是费了很大劲才发现的
4. 通用问题（任何项目都会遇到的）不存——只存这个项目特有的

判断标准 - 什么值得提取（必须同时满足）：
✓ 经过了实际验证（运行了代码、看了实际输出、确认问题解决）
✓ 不是显而易见的（推理2-3次想不到的）
✓ 是这个项目特有的（换个项目不一定遇到）
✓ 试了很多次才找到（不是一两次就试出来的）

不值得提取（任何一条都不存）：
✗ 通过推理就能想到的（如"检查配置"、"看报错信息"）
✗ 试错1-2次就解决的（如拼写错误、import遗漏）
✗ 通用问题（如"网络超时重试"、"内存不足清理缓存"）
✗ 没有实际验证的（只是猜测原因，没有确认）
✗ 没有实际解决的（失败了或搁置了）

返回 JSON 数组，每个元素：
- "problem": 这个项目的具体问题（不是通用问题类型）
  - 差："训练损失不下降"（通用）
  - 好："VOICE_voice_split项目的DDSPSeparator在vocal_audio_range>0.5时mask塌缩为0"
- "root_cause": 经过实际验证的根因（不是猜测）
  - 差："可能是梯度问题"（猜测）
  - 好："经验证，SI-SDR的scale-invariant特性在vocal和accompaniment相位差>90°时产生反向梯度"
- "solution": 经过验证的具体修法（包含精确参数）
  - 差："调整了损失权重"（模糊）
  - 好："将Waveform L1权重从0改为0.1，经3个epoch验证mask稳定在[0.2,0.8]"
- "verification": 如何验证有效的（具体看到了什么结果）
- "attempts_before_success": 之前试过什么失败了（体现难度）
- "why_not_obvious": 为什么推理想不到（体现非显而易见性）
- "importance": 1-10（10=极其难找且影响大，1=显而易见）
- "module": 相关模块名

只输出经过验证的发现。如果没有经过实际验证的发现，返回空数组 []。
宁可返回空数组，也不要存"推理几次就能想到"的废话。"""


def extract_solution_memories(project, session_uuid, interactions):
    """Extract solution-oriented memories from a session.

    Only saves verified, non-obvious, project-specific findings.
    Returns list of memory dicts worth saving.
    """
    if not interactions or len(interactions) < 3:
        return []

    client = llm.get()

    # Build session summary for the LLM
    summary_lines = []
    for i, item in enumerate(interactions[-50:]):  # Last 50 interactions
        tool = item.get("tool_name", item.get("hook_event", "?"))
        inp = ""
        resp_preview = ""
        try:
            raw_inp = item.get("tool_input", "")
            if isinstance(raw_inp, str):
                raw_inp = json.loads(raw_inp) if raw_inp else {}
            if isinstance(raw_inp, dict):
                if tool in ("Read", "Edit", "Write"):
                    inp = raw_inp.get("file_path", "")
                elif tool == "Bash":
                    inp = str(raw_inp.get("command", ""))[:100]
                elif tool == "UserPromptSubmit":
                    inp = str(raw_inp.get("prompt", ""))[:200]
                else:
                    inp = str(raw_inp)[:80]
        except:
            inp = str(item.get("tool_input", ""))[:80]

        try:
            raw_resp = item.get("tool_response", "")
            if isinstance(raw_resp, str):
                resp_preview = raw_resp[:150]
        except:
            pass

        status = item.get("status", "")
        summary_lines.append(f"[{i}] [{tool}] {inp} → {status}")
        if resp_preview:
            summary_lines.append(f"    响应: {resp_preview[:100]}")

    prompt = f"""项目: {project}
会话交互记录（共 {len(interactions)} 条，显示最后 {min(50, len(interactions))} 条）:
{chr(10).join(summary_lines)}

分析这次会话，只找出经过实际验证、确实有效的解决方案。
忽略所有可以通过推理或简单试错得到的发现。"""

    try:
        raw = client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=SOLUTION_EXTRACTION_SYSTEM,
            temperature=0.2,
        )
        data = client.extract_json(raw)
        if not data or not isinstance(data, list):
            return []

        memories = []
        for item in data:
            if not isinstance(item, dict):
                continue
            problem = item.get("problem", "")
            solution = item.get("solution", "")
            if not problem or not solution:
                continue

            importance = item.get("importance", 5)
            if importance < 6:  # Only importance >= 6 (not easily found)
                continue

            # Build structured narrative
            narrative = f"问题: {problem}"
            narrative += f"\n根因: {item.get('root_cause', '未知')}"
            narrative += f"\n解法: {solution}"
            verification = item.get("verification", "")
            if verification:
                narrative += f"\n验证: {verification}"
            attempts = item.get("attempts_before_success", "")
            if attempts:
                narrative += f"\n之前尝试失败: {attempts}"
            why = item.get("why_not_obvious", "")
            if why:
                narrative += f"\n为什么不容易发现: {why}"

            facts = [solution]
            if verification:
                facts.append(f"验证方法: {verification}")

            module_name = item.get("module", "")
            module_id = None
            if module_name and module_name.strip():
                module_name = module_name.strip().lower().replace(" ", "-").replace("_", "-")
                module_id = db.get_or_create_module(project, module_name, "")

            memories.append({
                "category": "observation",
                "obs_type": "discovery",
                "memory_type": "reflective",
                "title": f"验证解法: {problem[:50]}",
                "narrative": narrative,
                "facts": facts,
                "concepts": [module_name] if module_name else [],
                "importance": importance,
                "module_id": module_id,
            })

        return memories

    except Exception as e:
        _log.error(f"Solution extraction failed: {e}")
        return []


def process_solution_extraction(project, session_uuid, interactions):
    """Extract and save solution-oriented memories from a session."""
    memories = extract_solution_memories(project, session_uuid, interactions)
    saved = 0
    for mem in memories:
        try:
            # Use _save_observation to get linking logic
            mem_id = _save_observation(project, mem, session_uuid)
            if mem_id:
                saved += 1
                _log.info(f"Solution memory saved: id={mem_id} title={mem.get('title', '')[:50]}")
        except Exception as e:
            _log.error(f"Failed to save solution memory: {e}")

    return saved


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

    # Quality assessment: filter out low-quality memories before saving
    should_store, quality_score, assessment = _assess_memory_quality(data, project)
    if not should_store:
        _log.info(f"Memory rejected by quality assessment: score={quality_score} reason={assessment.get('reason', '')} title={data.get('title', '')[:40]}")
        return None
    # Use assessment score to adjust importance
    if quality_score > 0:
        importance = max(importance, int(quality_score))

    valid_categories = ("observation", "user", "reference", "feedback", "project")
    if category not in valid_categories:
        category = "observation"
    valid_types = ("episodic", "semantic", "procedural", "reflective")
    if memory_type not in valid_types:
        memory_type = "episodic"

    metadata = json.dumps({"quality": quality, "assessment": assessment}, ensure_ascii=False)

    # Module assignment
    module_name = data.get("module")
    module_id = None
    if module_name:
        # Normalize module name
        module_name = module_name.strip().lower().replace(" ", "-").replace("_", "-")
        module_desc = data.get("module_description", "")
        module_id = db.get_or_create_module(project, module_name, module_desc)

    # Find related memories using embedding + LLM
    related_ids = []
    related_map = {}  # id -> relationship description
    try:
        related_results = _find_related_memories(data, project, limit=3)
        if related_results:
            related_ids = [r[0] for r in related_results]
            related_map = {r[0]: r[1] for r in related_results}
    except Exception as e:
        _log.debug(f"Related memory search failed: {e}")

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

    # Add reverse links with relationship descriptions
    if related_ids:
        _add_reverse_links(mem_id, related_ids)
        # Log relationships
        for rid, rel in related_map.items():
            _log.info(f"Memory #{mem_id} related to #{rid}: {rel}")

    # Memory evolution: check if this triggers updates to existing memories
    try:
        _evolve_memories(mem_id, data, project)
    except Exception as e:
        _log.debug(f"Memory evolution skipped: {e}")

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


def _find_related_memories(new_memory, project, limit=5):
    """Find related memories using embedding similarity, then LLM to judge.

    Step 1: Use embedding to find top candidates (fast)
    Step 2: Use LLM to judge which are truly related and how (accurate)
    Returns list of (related_id, relationship_description) tuples.
    """
    try:
        # Step 1: Embedding similarity search
        text = f"{new_memory.get('title', '')} {new_memory.get('narrative', '')}"
        facts = new_memory.get("facts", [])
        if facts:
            text += " " + " ".join(facts[:3])

        candidates = db.search_by_keywords(
            text.split()[:5],  # Use top keywords
            project=project,
            limit=limit * 2  # Get more candidates for LLM filtering
        )

        if not candidates:
            _log.debug(f"No keyword candidates found for: {new_memory.get('title', '')[:30]}")
            return []

        # Also try vector search if embedding is available
        try:
            from lib import retriever
            vec_results = retriever.search(
                text, project=project, limit=limit * 2, use_vector=True
            )
            if vec_results:
                # Merge with keyword results, dedup by id
                seen_ids = {c["id"] for c in candidates}
                for v in vec_results:
                    if v["id"] not in seen_ids:
                        candidates.append(v)
                        seen_ids.add(v["id"])
        except Exception as e:
            _log.debug(f"Vector search failed: {e}")

        if not candidates:
            _log.debug(f"No candidates after merge for: {new_memory.get('title', '')[:30]}")
            return []

        # Step 2: LLM judgment
        client = llm.get()
        candidate_text = []
        # Convert sqlite3.Row to dict for safe access
        candidate_dicts = []
        for c in candidates[:limit * 2]:
            cd = dict(c) if not isinstance(c, dict) else c
            candidate_dicts.append(cd)
            candidate_text.append(f"[#{cd['id']}] {cd.get('title', '')}: {(cd.get('narrative', '') or '')[:100]}")

        prompt = f"""新记忆:
标题: {new_memory.get('title', '')}
内容: {new_memory.get('narrative', '')[:200]}

候选相关记忆:
{chr(10).join(candidate_text)}

判断新记忆与哪些候选记忆相关，以及它们之间的关系。

返回 JSON 数组，每个元素:
- "id": 相关记忆的 ID
- "relationship": 关系描述（如"同一问题的不同方面"、"因果关系"、"对比方案"）
- "importance_delta": 新记忆相对于相关记忆的重要性变化（-2到+2）

只返回真正相关的（关系明确的），不相关的不要返回。"""
        raw = client.chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是记忆关联分析器。判断两条记忆之间的关系。只输出 JSON 数组。",
            temperature=0.1,
        )
        data = client.extract_json(raw)
        if not data or not isinstance(data, list):
            _log.debug(f"LLM returned no related memories for: {new_memory.get('title', '')[:30]}")
            return []

        results = []
        for item in data:
            if isinstance(item, dict) and "id" in item and "relationship" in item:
                related_id = item["id"]
                # Verify the related memory exists
                if any(cd["id"] == related_id for cd in candidate_dicts):
                    results.append((related_id, item["relationship"]))

        _log.debug(f"Found {len(results)} related memories for: {new_memory.get('title', '')[:30]}")
        return results

        return results[:limit]

    except Exception as e:
        _log.warning(f"Find related memories failed: {e}")
        return []


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


# ── Memory Evolution (A-Mem inspired) ───────────────────────────

EVOLUTION_SYSTEM = """你是一个记忆演化器。分析新记忆与已有记忆的关系，决定是否需要更新旧记忆。

核心原则：新记忆存入时，可能需要更新、合并或标记旧记忆。

返回 JSON 对象：
- "should_update": 是否需要更新旧记忆（布尔值）
- "updates": 数组，每个元素包含：
  - "memory_id": 需要更新的记忆 ID
  - "action": "merge" / "supersede" / "link" / "none"
  - "reason": 为什么需要这个操作
  - "new_narrative": 如果是 merge/supersede，新的 narrative 应该是什么
  - "new_importance": 如果变化，新的重要性评分

判断标准：
- merge: 新记忆是旧记忆的补充或细化（如同一问题的更深入发现）
- supersede: 新记忆推翻了旧记忆的结论（如"之前认为是A原因，现在确认是B"）
- link: 新记忆与旧记忆相关但独立（如同一项目不同问题）
- none: 无关

只输出有效 JSON。"""


QUALITY_ASSESSMENT_SYSTEM = """你是一个记忆质量评估器。评估这条记忆是否值得存储。

评估维度（每项 0-10 分）：
1. "verified": 是否经过实际验证（运行代码、看实际输出、确认有效）
   - 0=只是猜测， 5=部分验证， 10=完全验证
2. "difficulty": 推理几次能想到（1=很容易，10=极其困难）
   - 1=显而易见， 3=试1-2次， 5=需要深入分析， 8=非常隐蔽， 10=极其罕见
3. "specificity": 项目特有程度（0=通用问题，10=只有这个项目会遇到）
   - 0=任何项目都会遇到， 5=特定技术栈， 10=这个项目独有的配置/架构
4. "actionability": 可执行程度（0=纯理论，10=可以直接照做）
   - 0=泛泛而谈， 5=有方向但需调整， 10=有具体参数可直接用

综合分 = (verified × 0.3) + (difficulty × 0.3) + (specificity × 0.2) + (actionability × 0.2)

返回 JSON：
- "verified": 0-10
- "difficulty": 1-10
- "specificity": 0-10
- "actionability": 0-10
- "total": 综合分
- "should_store": 综合分 >= 6
- "reason": 为什么值得/不值得存

只输出有效 JSON。"""


def _evolve_memories(new_memory_id, new_memory, project):
    """Check if new memory should trigger updates to existing memories.

    This is the A-Mem inspired evolution mechanism.
    """
    try:
        # Find similar memories
        similar = db.find_similar_reasoning_chains(
            new_memory.get("title", "") + " " + (new_memory.get("narrative", "") or ""),
            project,
            limit=5
        )

        # Also search by keywords
        title_words = new_memory.get("title", "").split()[:5]
        if title_words:
            keyword_results = db.search_by_keywords(title_words, project=project, limit=5)
            # Merge and dedup
            seen_ids = {s[0]["id"] for s in similar}
            for kr in keyword_results:
                if kr["id"] not in seen_ids:
                    similar.append((dict(kr), 0))
                    seen_ids.add(kr["id"])

        if not similar:
            return

        # Build context for LLM
        new_text = f"新记忆: {new_memory.get('title', '')}\n{(new_memory.get('narrative', '') or '')[:200]}"

        existing_text = []
        for mem, rank in similar[:5]:
            existing_text.append(f"[#{mem['id']}] {mem.get('title', '')}: {(mem.get('narrative', '') or '')[:100]}")

        client = llm.get()
        prompt = f"""{new_text}

已有记忆:
{chr(10).join(existing_text)}

分析新记忆与已有记忆的关系，决定是否需要更新。"""

        raw = client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=EVOLUTION_SYSTEM,
            temperature=0.1,
        )
        data = client.extract_json(raw)
        if not data or not isinstance(data, dict):
            return

        if not data.get("should_update"):
            return

        updates = data.get("updates", [])
        for update in updates:
            if not isinstance(update, dict):
                continue
            mem_id = update.get("memory_id")
            action = update.get("action", "none")
            if not mem_id or action == "none":
                continue

            if action == "supersede":
                # Mark old memory as superseded
                new_narrative = update.get("new_narrative", "")
                new_importance = update.get("new_importance")
                if new_narrative:
                    db.update_memory(mem_id, narrative=new_narrative)
                if new_importance:
                    db.update_memory(mem_id, importance=new_importance)
                # Add link from old to new
                existing = db.get_memory(mem_id)
                if existing:
                    related = json.loads(existing.get("related_to", "[]") or "[]")
                    if new_memory_id not in related:
                        related.append(new_memory_id)
                        db.update_memory(mem_id, related_to=json.dumps(related, ensure_ascii=False))
                _log.info(f"Memory #{mem_id} superseded by #{new_memory_id}: {update.get('reason', '')}")

            elif action == "merge":
                # Merge new info into old memory
                new_narrative = update.get("new_narrative", "")
                if new_narrative:
                    db.update_memory(mem_id, narrative=new_narrative)
                new_importance = update.get("new_importance")
                if new_importance:
                    db.update_memory(mem_id, importance=new_importance)
                _log.info(f"Memory #{mem_id} merged with #{new_memory_id}: {update.get('reason', '')}")

            elif action == "link":
                # Just add a link
                existing = db.get_memory(mem_id)
                if existing:
                    related = json.loads(existing.get("related_to", "[]") or "[]")
                    if new_memory_id not in related:
                        related.append(new_memory_id)
                        db.update_memory(mem_id, related_to=json.dumps(related, ensure_ascii=False))
                _log.info(f"Memory #{mem_id} linked to #{new_memory_id}: {update.get('reason', '')}")

    except Exception as e:
        _log.debug(f"Memory evolution failed: {e}")


def _assess_memory_quality(memory_data, project):
    """Assess memory quality before saving.

    Returns (should_store, quality_score, assessment_details).
    """
    try:
        client = llm.get()
        prompt = f"""评估这条记忆是否值得存储：

标题: {memory_data.get('title', '')}
内容: {(memory_data.get('narrative', '') or '')[:300]}
事实: {memory_data.get('facts', [])[:3]}
项目: {project}"""

        raw = client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=QUALITY_ASSESSMENT_SYSTEM,
            temperature=0.1,
        )
        data = client.extract_json(raw)
        if not data or not isinstance(data, dict):
            return True, 5, {"reason": "评估失败，默认通过"}

        should_store = data.get("should_store", True)
        total = data.get("total", 5)

        return should_store, total, data

    except Exception as e:
        _log.debug(f"Quality assessment failed: {e}")
        return True, 5, {"reason": f"评估异常: {e}"}


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

    # Skip if LLM says this isn't worth saving
    if data.get("skip"):
        _log.debug(f"Reasoning chain skipped (skip=true): {data.get('question', '')[:60]}")
        return None

    outcome = data.get("outcome", "pending")
    importance = data.get("importance", 5)
    question = data.get("question", user_message[:200])
    steps = data.get("steps", [])
    failure_reason = data.get("failure_reason", "")

    # Filter: skip chains with no meaningful content
    if not steps and outcome == "pending":
        _log.debug(f"Reasoning chain skipped (no steps, pending): {question[:60]}")
        return None

    # Filter: skip low-importance chains (importance <= 3)
    if importance <= 3:
        _log.debug(f"Reasoning chain skipped (low importance={importance}): {question[:60]}")
        return None

    # Filter: skip failure chains with no actionable information
    if outcome == "failure" and not failure_reason:
        _log.debug(f"Reasoning chain skipped (failure without reason): {question[:60]}")
        return None

    # Check for similar existing chains and merge instead of duplicate
    similar = db.find_similar_reasoning_chains(question, project, limit=3)
    for existing_chain, rank in similar:
        # If very similar (rank close to 0 = very similar in FTS), merge
        if rank > -2.0:  # FTS rank is negative, closer to 0 = more similar
            db.merge_reasoning_chains(existing_chain["id"], {
                "outcome": outcome,
                "outcome_summary": data.get("outcome_summary"),
                "failure_reason": failure_reason,
                "extracted_facts": data.get("extracted_facts", []),
                "steps": steps,
                "importance": importance,
            })
            _log.info(f"Reasoning chain merged into #{existing_chain['id']}: {question[:60]}")
            return {"id": existing_chain["id"], "outcome": outcome, "question": question, "merged": True}

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
        failure_reason=failure_reason,
        extracted_facts=data.get("extracted_facts", []),
        session_uuid=session_uuid,
        importance=importance,
    )

    # Only save extracted facts as memories if they contain actionable knowledge
    facts = data.get("extracted_facts", [])
    if facts and importance >= 7:
        # Filter facts: only keep ones that are actionable or contain specific solutions
        actionable_facts = [f for f in facts if any(kw in f.lower() for kw in
            ["修复", "解决", "方案", "配置", "设置", "安装", "部署", "优化",
             "fix", "solve", "solution", "config", "install", "deploy", "optimize",
             "应该", "需要", "避免", "注意", "关键", "核心", "原理"])]
        if len(actionable_facts) >= 2:
            db.insert_memory(
                project=project,
                category="observation",
                obs_type="discovery",
                memory_type="procedural",  # Mark as procedural since it contains solutions
                title=f"解决方案: {question[:50]}",
                narrative=f"问题: {question[:100]}\n方案: {'; '.join(actionable_facts[:3])}",
                facts=actionable_facts,
                origin_session=session_uuid,
                generated_by=config_model_name(),
                importance=importance,
            )

    _log.info(f"Reasoning chain extracted: id={chain_id} outcome={outcome} steps={len(steps)}")
    return {"id": chain_id, "outcome": outcome, "question": question}
