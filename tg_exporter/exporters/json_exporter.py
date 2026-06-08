"""
JsonExporter — потоковая запись сообщений в JSON.

Пишет в файл инкрементально (без накопления в памяти).
Совместим с текущим форматом result.json.
"""

from __future__ import annotations

import json
from typing import Optional, IO

from .base import BaseExporter
from ..models.message import ExportMessage


class JsonExporter(BaseExporter):
    """
    Создаёт файл result.json вида:
    {
      "name": "Chat Name",
      "topic": "Topic Title",   // опционально
      "messages": [
        { ... },
        { ... }
      ]
    }
    """

    def __init__(self, include_views: bool = True) -> None:
        super().__init__()
        self._include_views = include_views
        self._file: Optional[IO[str]] = None
        self._first = True
        self._output_path: Optional[str] = None

    def _open(self) -> None:
        self._output_path = self._path("result.json")
        self._file = open(self._output_path, "w", encoding="utf-8")
        self._first = True

        header = '{\n  "name": ' + json.dumps(self._chat_name, ensure_ascii=False)
        if self._topic_title:
            header += ',\n  "topic": ' + json.dumps(self._topic_title, ensure_ascii=False)
        header += ',\n  "messages": [\n'
        self._file.write(header)

    def write(self, msg: ExportMessage) -> None:
        assert self._file is not None, "open() must be called first"

        d = msg.to_dict()
        if not self._include_views:
            d.pop("views", None)
            d.pop("forwards", None)

        if not self._first:
            self._file.write(",\n")
        self._first = False

        json.dump(d, self._file, ensure_ascii=False)

    def finalize(self) -> list[str]:
        if self._file is not None:
            self._file.write('\n  ]\n}\n')
            self._file.close()
            self._file = None
        if self._output_path:
            self._register(self._output_path)
        return self.output_files

    def close(self) -> None:
        """
        Закрывает файл при отмене, ДОПИСЫВАЯ закрывающие скобки, чтобы
        частичный экспорт остался валидным JSON и мог быть открыт любыми
        парсерами. Потерянные сообщения — последние в iter-е, всё что
        успели записать — корректный массив.
        """
        if self._file is not None:
            try:
                try:
                    self._file.write('\n  ]\n}\n')
                except Exception:
                    pass
                self._file.close()
            except Exception:
                pass
            self._file = None
        if self._output_path:
            self._register(self._output_path)
