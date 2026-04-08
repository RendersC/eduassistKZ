FROM python:3.11-slim

# Рабочая директория внутри контейнера
WORKDIR /app

# Копируем зависимости отдельно для кэширования слоёв
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY bot.py .

# База данных будет создана в этой же директории при первом запуске
VOLUME ["/app"]

CMD ["python", "bot.py"]
