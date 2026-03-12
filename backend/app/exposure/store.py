from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.policies.schemas import ExposureContext

TTL_SECONDS = 48 * 60 * 60
CENT = Decimal("0.01")


class ExposureStoreUnavailableError(RuntimeError):
    pass


class ExposureStore(Protocol):
    def get_exposure(self, action_type: str, user_id: str, date: date_type) -> ExposureContext:
        ...

    def apply_allow(self, action_type: str, user_id: str, amount: Decimal, date: date_type) -> ExposureContext:
        ...

    def get_financial_total(self, date: date_type) -> int:
        ...

    def increment_financial_total(self, amount: Decimal, date: date_type) -> int:
        ...

    def increment_action_rate(self, action_type: str, minute_bucket: str) -> int:
        ...


@dataclass
class RedisExposureStore:
    client: Redis

    @classmethod
    def from_settings(cls) -> "RedisExposureStore":
        return cls(client=Redis.from_url(get_settings().redis_url, decode_responses=True))

    def get_exposure(self, action_type: str, user_id: str, date: date_type) -> ExposureContext:
        try:
            date_bucket = date.isoformat()
            daily_total_raw = self.client.get(_daily_total_key(action_type, date_bucket))
            per_user_amount_raw = self.client.get(_per_user_amount_key(action_type, user_id, date_bucket))
            per_user_count_raw = self.client.get(_per_user_count_key(action_type, user_id, date_bucket))
            financial_total_raw = self.client.get(_financial_total_key(date_bucket))
        except RedisError as exc:
            raise ExposureStoreUnavailableError("Redis unavailable") from exc

        return ExposureContext(
            daily_total_amount=_cents_to_decimal(daily_total_raw),
            per_user_daily_amount=_cents_to_decimal(per_user_amount_raw),
            per_user_daily_count=int(per_user_count_raw or 0),
            financial_total_amount_cents=int(financial_total_raw or 0),
        )

    def apply_allow(self, action_type: str, user_id: str, amount: Decimal, date: date_type) -> ExposureContext:
        try:
            date_bucket = date.isoformat()
            amount_cents = _decimal_to_cents(amount)
            daily_total_key = _daily_total_key(action_type, date_bucket)
            per_user_amount_key = _per_user_amount_key(action_type, user_id, date_bucket)
            per_user_count_key = _per_user_count_key(action_type, user_id, date_bucket)

            with self.client.pipeline(transaction=True) as pipeline:
                pipeline.incrby(daily_total_key, amount_cents)
                pipeline.expire(daily_total_key, TTL_SECONDS)
                pipeline.incrby(per_user_amount_key, amount_cents)
                pipeline.expire(per_user_amount_key, TTL_SECONDS)
                pipeline.incr(per_user_count_key)
                pipeline.expire(per_user_count_key, TTL_SECONDS)
                results = pipeline.execute()
        except RedisError as exc:
            raise ExposureStoreUnavailableError("Redis unavailable") from exc

        return ExposureContext(
            daily_total_amount=_cents_to_decimal(results[0]),
            per_user_daily_amount=_cents_to_decimal(results[2]),
            per_user_daily_count=int(results[4]),
        )

    def get_financial_total(self, date: date_type) -> int:
        try:
            value = self.client.get(_financial_total_key(date.isoformat()))
        except RedisError as exc:
            raise ExposureStoreUnavailableError("Redis unavailable") from exc
        return int(value or 0)

    def increment_financial_total(self, amount: Decimal, date: date_type) -> int:
        try:
            financial_total_key = _financial_total_key(date.isoformat())
            amount_cents = _decimal_to_cents(amount)

            with self.client.pipeline(transaction=True) as pipeline:
                pipeline.incrby(financial_total_key, amount_cents)
                pipeline.expire(financial_total_key, TTL_SECONDS)
                results = pipeline.execute()
        except RedisError as exc:
            raise ExposureStoreUnavailableError("Redis unavailable") from exc
        return int(results[0])

    def increment_action_rate(self, action_type: str, minute_bucket: str) -> int:
        try:
            key = _action_rate_key(action_type, minute_bucket)
            with self.client.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, 2 * 60)
                results = pipeline.execute()
        except RedisError as exc:
            raise ExposureStoreUnavailableError("Redis unavailable") from exc
        return int(results[0])


def get_exposure_store() -> ExposureStore:
    return RedisExposureStore.from_settings()


def _daily_total_key(action_type: str, date_bucket: str) -> str:
    return f"exposure:{action_type}:{date_bucket}:total_amount"


def _per_user_amount_key(action_type: str, user_id: str, date_bucket: str) -> str:
    return f"exposure:{action_type}:user:{user_id}:{date_bucket}:amount"


def _per_user_count_key(action_type: str, user_id: str, date_bucket: str) -> str:
    return f"exposure:{action_type}:user:{user_id}:{date_bucket}:count"


def _financial_total_key(date_bucket: str) -> str:
    return f"exposure:financial_total:{date_bucket}"


def _action_rate_key(action_type: str, minute_bucket: str) -> str:
    return f"exposure:rate:{action_type}:{minute_bucket}:count"


def _decimal_to_cents(amount: Decimal) -> int:
    return int((amount / CENT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _cents_to_decimal(value: str | int | None) -> Decimal:
    cents = Decimal(str(value or 0))
    return (cents * CENT).quantize(CENT)
