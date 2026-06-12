"""命令行界面"""
import sys, json
from lib import db, retriever, fusion, worker, config, logger

_log = logger.get()

USAGE = """记忆系统 CLI

用法: python main.py cli <命令> [参数]

命令:
  stats                  显示记忆统计
  list [项目] [数量]     列出记忆
  search <查询> [项目]   搜索记忆
  create <项目> <标题> [描述]  手动创建记忆
  delete <ID>            停用记忆
  sessions [项目]        列出会话
  knowledge <项目>       显示知识条目
  fusion [项目]          运行融合周期
  worker start|stop      控制后台工作线程
  queue                  显示队列状态
  serve                  启动 Web 服务器
  init                   初始化数据库
"""


def main():
    args = sys.argv[1:]
    if not args:
        print(USAGE)
        return

    cmd = args[0]
    db.connect()

    if cmd == "stats":
        s = db.stats()
        print(json.dumps(s, indent=2, ensure_ascii=False))

    elif cmd == "list":
        project = args[1] if len(args) > 1 else None
        limit = int(args[2]) if len(args) > 2 else 20
        memories = db.list_memories(project=project, limit=limit)
        if not memories:
            print("暂无记忆")
        for m in memories:
            facts = ""
            try:
                fl = json.loads(m["facts"]) if m["facts"] else []
                if fl:
                    facts = f" | 事实: {', '.join(fl[:2])}"
            except (json.JSONDecodeError, TypeError):
                pass
            print(f"[{m['id']}] [{m['category']}/{m['obs_type'] or '-'}] {m['title'] or m['name'] or ''} ({m['project']}){facts}")

    elif cmd == "search":
        if len(args) < 2:
            print("用法: search <查询> [项目]")
            return
        query = args[1]
        project = args[2] if len(args) > 2 else None
        results = retriever.search(query, project=project)
        if not results:
            print("未找到匹配结果")
        for m in results:
            print(f"[{m['id']}] [{m['category']}] {m['title'] or m['name'] or ''} - {(m['narrative'] or '')[:80]}")

    elif cmd == "create":
        if len(args) < 3:
            print("用法: create <项目> <标题> [描述]")
            return
        project, title = args[1], args[2]
        narrative = args[3] if len(args) > 3 else None
        mid = db.insert_memory(project=project, category="observation", title=title, narrative=narrative)
        print(f"已创建记忆 #{mid}")

    elif cmd == "delete":
        if len(args) < 2:
            print("用法: delete <ID>")
            return
        db.deactivate_memory(int(args[1]), reason="CLI 手动删除")
        print(f"已停用记忆 #{args[1]}")

    elif cmd == "sessions":
        project = args[1] if len(args) > 1 else None
        sessions = db.get_sessions(project=project)
        if not sessions:
            print("暂无会话")
        for s in sessions:
            print(f"[{s['session_uuid'][:8]}] {s['project']} | {s['status']} | 工具调用={s['tool_count']} | {s['started_at']}")

    elif cmd == "knowledge":
        if len(args) < 2:
            print("用法: knowledge <项目>")
            return
        entries = db.get_knowledge(args[1])
        if not entries:
            print("暂无知识条目")
        for e in entries:
            print(f"  {e['key']}: {e['value']}")

    elif cmd == "fusion":
        project = args[1] if len(args) > 1 else None
        print("正在运行融合周期...")
        stats = fusion.run_fusion_cycle(project=project)
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif cmd == "worker":
        if len(args) < 2:
            print("用法: worker start|stop")
            return
        if args[1] == "start":
            worker.start()
            print("工作线程已启动，按 Ctrl+C 停止")
            import time
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                worker.stop()
                print("工作线程已停止")
        elif args[1] == "stop":
            worker.stop()
            print("工作线程已停止")

    elif cmd == "queue":
        stats = db.queue_stats()
        if not stats:
            print("队列为空")
        for s in stats:
            print(f"  {s['status']}: {s['cnt']}")

    elif cmd == "serve":
        from lib import server
        cfg = config.get("server") or {}
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 38800)
        worker.start()
        srv = server.start_server(host, port)
        print(f"服务运行于 http://{host}:{port}")
        print("按 Ctrl+C 停止")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()
            srv.shutdown()
            print("已停止")

    elif cmd == "init":
        print("数据库已初始化")

    else:
        print(f"未知命令: {cmd}")
        print(USAGE)


if __name__ == "__main__":
    main()
