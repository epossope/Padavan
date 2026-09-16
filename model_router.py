"""Runtime model helpers.

Chat routing is global by design.  The legacy per-user primary/fallback
columns remain readable only for an on-disk migration window; they are never
consulted for a normal LLM request.
"""
from __future__ import annotations


class ModelRouter:
    def __init__(self, connect, default_model, fallback_models, vision_model):
        self.connect = connect
        self.default_model = default_model
        self.fallback_models = fallback_models
        self.vision_model = vision_model

    def resolve(self, chat_id, task_type="chat"):
        try:
            with self.connect() as c:
                row = c.execute("SELECT primary_model, fallback_model, vision_model FROM user_settings WHERE chat_id=?", (chat_id,)).fetchone()
        except Exception:
            row = None
        row = dict(row) if row else {}
        if task_type == "vision":
            return row.get("vision_model") or self.vision_model
        return {"primary": self.default_model,
                "fallback": self.fallback_models[0] if self.fallback_models else ""}

    def set_primary(self, chat_id, model):
        """Compatibility no-op: personal chat model selection was retired."""
        with self.connect() as c:
            c.execute("INSERT INTO user_settings(chat_id, primary_model, fallback_model) VALUES(?, '', '') "
                      "ON CONFLICT(chat_id) DO UPDATE SET primary_model='', fallback_model=''", (chat_id,))

    def set_vision(self, chat_id, model):
        """Store a user's preferred Vision model.

        Authorization for using this preference is enforced by bot.py: a
        personal API key must be active.  This keeps a leftover preference
        harmless after a user removes their key.
        """
        with self.connect() as c:
            c.execute("INSERT INTO user_settings(chat_id, vision_model) VALUES(?, ?) "
                      "ON CONFLICT(chat_id) DO UPDATE SET vision_model=excluded.vision_model", (chat_id, model))
