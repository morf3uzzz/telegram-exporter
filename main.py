"""
main.py — точка входа нового приложения.

Запуск:
    python main.py

Старый app.py остаётся нетронутым как fallback.
"""

import sys
import traceback


# Python 3.13+ для Tkinter по умолчанию включает Per-Monitor V2 DPI awareness
# на Windows. На Win11 это даёт регрессию: при смене effective DPI монитора
# (док/undock, переключение экранов, спящий внешний дисплей) свёрнутое окно
# при deiconify уезжает в координаты вне видимой области, и клик по таскбару
# не разворачивает его, хотя процесс жив. Принудительно ставим System DPI
# aware (как на 3.11, где CI автора и проблемы нет) ДО любых импортов Tk.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        pass


def main() -> None:
    try:
        from tg_exporter.ui.app import App
        app = App()
        app.mainloop()
    except Exception as exc:
        # Фатальная ошибка до старта UI
        tb = traceback.format_exc()
        try:
            from tg_exporter.utils.logger import logger
            logger.fatal("Fatal startup error", exc=exc)
        except Exception:
            pass
        # Показываем messagebox если возможно
        try:
            import tkinter.messagebox as mb
            mb.showerror("Ошибка запуска", f"{exc}\n\nПодробности в ~/.tg_exporter/app.log")
        except Exception:
            print(f"FATAL: {exc}\n{tb}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
