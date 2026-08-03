@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo Job Assistant - запуск локального сервера...
echo Отчёт откроется в браузере. Кнопки "Обновить" и "Экспорт" работают отсюда.
echo Закрой это окно (или Ctrl+C), чтобы остановить сервер.
python src\serve.py
pause
