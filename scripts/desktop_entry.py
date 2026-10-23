"""PyInstaller entry point; report startup errors without a console window."""

from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()  # Dispatch bounded discovery workers before any GUI import.
    from doppel_agent.desktop import main

    try:
        main()
    except Exception as exc:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, f"Doppel Agent 无法启动:\n{exc}", "Doppel Agent", 0x10
        )
        raise
