# Деплой Noema в Amvera

Конфигурация готова для Python 3.12: [amvera.yml](amvera.yml) запускает
`bot.py`, а SQLite, сохранённые вложения и рабочие данные автоматически пишутся
в `/data`. Эта директория Amvera не затирается при пересборке.

## Перед первым запуском

1. Создайте проект Python в Amvera и подключите этот Git-репозиторий.
2. В разделе переменных окружения задайте `TELEGRAM_BOT_TOKEN` и
   `OPENROUTER_API_KEY`. Для Mini App задайте публичный HTTPS origin в
   `QUICK_ACTIONS_BASE_URL`. Не загружайте `.env` и не коммитьте ключи.
3. Production voice использует `BATCH_STT_MODEL` и
   `BATCH_STT_TIMEOUT_SEC`. Существующий `STT_MODEL` поддерживается только
   как compatibility alias на один релиз и должен быть заменён новым именем.
4. `MISTRAL_API_KEY`, `MISTRAL_REALTIME_MODEL` и
   `MISTRAL_CLIENT_SESSIONS_URL` нужны только для admin-only Experimental
   Realtime Beta. Обычный tap-to-talk от них не зависит.
5. Полный перечень и статусы переменных — в [ENV_AUDIT.md](ENV_AUDIT.md).
6. Разверните проект. После первого успешного запуска, если переносится
   существующая память, загрузите `noema_test.sqlite3` в раздел **Data**
   Amvera как `/data/noema_test.sqlite3` и перезапустите приложение.

## Semantic-core release candidate (default-off)

The deploy-tree manifest includes the semantic-core modules required by the
existing `bot.py` imports and the default-off shadow path. The release does
not enable semantic execution.

1. In Amvera environment variables, set `SEMANTIC_RUNTIME_MODE=off` and leave
   `SEMANTIC_CANARY_USER_IDS` empty. Do not use `safe_write` or `full`.
2. To publish the reviewed `feature/semantic-core` source without merging it
   into `master`, run **Publish clean deploy branch** manually in GitHub
   Actions and select that branch/ref. The workflow builds the audited tree
   and records the selected source SHA in the linear `deploy` history.
3. Before switching Amvera, back up `/data/noema_test.sqlite3`. The process
   runs `init_db()` before Telegram polling and HTTP startup; it creates the
   `semantic_executions` table and its `(chat_id, request_id)` index if absent.
4. Deploy the resulting `deploy` branch in Amvera. Verify `NOEMA STARTED`,
   `DATA: /data`, normal legacy text reply, Mini App `/healthz`, and that the
   database contains `semantic_executions`. No semantic canary action is part
   of this release smoke check.

### Rollback

For any semantic concern, set `SEMANTIC_RUNTIME_MODE=off`, clear
`SEMANTIC_CANARY_USER_IDS`, and restart the service. The regular legacy stream
remains the active path. This rollback does not delete legacy code, user data,
or `semantic_executions` receipts.

### Approved global semantic rollout

For a reviewed rollout to every authenticated/trusted Telegram or signed Mini
App owner, set exactly:

```text
SEMANTIC_RUNTIME_MODE=full
SEMANTIC_CANARY_USER_IDS=*
```

`*` is global only when it is the entire trimmed value. Mixed values such as
`1,*` or `*,2` do not enable everyone; only valid numeric IDs in those values
remain allowlisted. `off` is always the global kill switch. Do not use this
setting for anonymous or unauthenticated traffic.

## Восстановление существующей памяти

Перед загрузкой базы остановите приложение. В разделе **Repository → Data**
загрузите локальный `noema_test.sqlite3` с тем же именем, подтвердите замену
пустого серверного файла и запустите деплой снова. В этой одной базе хранятся
заметки, люди, задачи, расходы, история и универсальная память.

## Проверка после деплоя

- В логах есть `NOEMA STARTED` и путь `/data` используется для базы.
- `/start` отвечает клавиатурой из шести основных пунктов.
- `⚙️ Настройки → 🧠 Модель`: выбор меняет следующий LLM-запрос без рестарта.
- Отправьте длинный ответ и Markdown: звёздочки не видны, текст приходит
  несколькими целыми сообщениями.

Amvera рекомендует хранить изменяемые данные в постоянном `/data`, а для
Python-бота указывать `requirements.txt` и файл входа. См. официальные
[инструкции для Telegram-бота](https://docs.amvera.ru/general/examples/python-tgbot.html)
и [описание Python-конфигурации](https://docs.amvera.ru/applications/environments/python-pip.html).
