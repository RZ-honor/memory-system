"""Main entry point for the memory system."""
import sys, os

# Ensure lib is importable
sys.path.insert(0, os.path.dirname(__file__))

from lib import db, config, logger

def main():
    if len(sys.argv) < 2:
        print("用法: python main.py <命令>")
        print("命令: serve, hook, worker, cli, init")
        return

    cmd = sys.argv[1]
    sys.argv = sys.argv[1:]  # Shift argv for sub-modules

    if cmd == "serve":
        from lib import server, worker
        db.connect()
        worker.start()
        cfg = config.get("server") or {}
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 38800)
        srv = server.start_server(host, port)
        logger.get().info(f"Memory system running at http://{host}:{port}")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()
            srv.shutdown()

    elif cmd == "hook":
        from lib import hook
        hook.handle_hook_event()

    elif cmd == "user-prompt":
        from lib import hook
        hook.handle_user_prompt_submit()

    elif cmd == "session-end":
        from lib import hook
        session_id = sys.argv[2] if len(sys.argv) > 2 else None
        hook.handle_session_end(session_id)

    elif cmd == "worker":
        from lib import worker
        db.connect()
        worker.start()
        logger.get().info("工作线程已启动，按 Ctrl+C 停止")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop()

    elif cmd == "cli":
        from lib import cli
        cli.main()

    elif cmd == "init":
        db.connect()
        print("数据库初始化成功")

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
