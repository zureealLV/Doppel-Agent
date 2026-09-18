"""PyInstaller entry point; report startup errors without a console window."""

from doppel_agent.desktop import main


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, f"Doppel Agent 无法启动:\n{exc}", "Doppel Agent", 0x10
        )
        raise
