# Analytic System for Distributed RAG

Система поднимает:

- `backend`: Python/FastAPI API для аналитики, заглушек управления RAG и экспорта метрик.
- `prometheus`: сбор метрик с backend и трех удаленных `node_exporter`.
- `grafana`: дашборд по ресурсам серверов и synthetic RAG probes.
- `frontend`: VueJS UI для просмотра Grafana, live-метрик и изменения параметров RAG.

## Быстрый старт

1. Скопировать `.env.example` в `.env` и задать реальные адреса:
   - `RAG_BACKEND_URL`
   - `RAG_API_SECRET`
   - `RAG_EXPORTER_TARGET`
   - `OLLAMA_EXPORTER_TARGET`
   - `EMBEDDING_EXPORTER_TARGET`
2. Убедиться, что на трех удаленных серверах доступен `node_exporter` на указанных адресах.
3. Запустить:

```bash
docker compose up --build
```

## Что работает

- Grafana показывает CPU, память, диск и сеть трех серверов.
- Backend циклически отправляет synthetic probe в RAG backend (генерация рекомендаций) и сохраняет результат.
- Frontend показывает throughput, success rate, quality score, live feed ответов и форму для изменения RAG-промптов.
- Обновление RAG-промпта пытается вызвать удаленный update endpoint. Если endpoint недоступен, промпт все равно сохраняется локально как заглушка.

## Основные endpoints backend

- `GET /api/overview`
- `GET /api/rag/config`
- `PUT /api/rag/config`
- `GET /api/quality/live`
- `POST /api/quality/probe`
- `GET /metrics`
