from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KillSwitch(Base):
    __tablename__ = "kill_switch"
    __table_args__ = (CheckConstraint("id = 1", name="ck_kill_switch_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
