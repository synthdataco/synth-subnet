import os
from datetime import datetime
import urllib

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    BigInteger,
    DateTime,
    Float,
    String,
    JSON,
    ForeignKey,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship, Session

from synth.utils.logging import silence_noisy_loggers


class Base(DeclarativeBase):
    """Our root for all ORM models."""

    pass


def get_database_url():
    load_dotenv()
    password = urllib.parse.quote_plus(os.getenv("POSTGRES_PASSWORD") or "")
    return (
        f"postgresql://{os.getenv('POSTGRES_USER')}:"
        f"{password}@"
        f"{os.getenv('POSTGRES_HOST')}:"
        f"{os.getenv('POSTGRES_PORT')}/"
        f"{os.getenv('POSTGRES_DB')}"
    )


def create_engine_and_session():
    silence_noisy_loggers()
    engine = create_engine(get_database_url(), echo=False, pool_pre_ping=True)
    return engine, Session(engine)


def get_engine():
    engine, _ = create_engine_and_session()
    return engine


class ValidatorRequest(Base):
    __tablename__ = "validator_requests"

    id = Column(BigInteger, primary_key=True)
    start_time: datetime = Column(DateTime(timezone=True), nullable=False)
    asset = Column(String, nullable=True)
    time_increment = Column(Integer, nullable=True)
    time_length = Column(Integer, nullable=True)
    num_simulations = Column(Integer, nullable=True)
    request_time = Column(DateTime(timezone=True), nullable=True)
    real_prices = Column(JSON, nullable=True)

    # backref from MinerPrediction
    predictions = relationship("MinerPrediction", back_populates="request")


class Miner(Base):
    __tablename__ = "miners"

    id = Column(BigInteger, primary_key=True)
    miner_uid = Column(Integer, nullable=False)
    coldkey = Column(String, nullable=True)
    hotkey = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    predictions = relationship("MinerPrediction", back_populates="miner")
    rewards = relationship("MinerReward", back_populates="miner")


class MinerPrediction(Base):
    __tablename__ = "miner_predictions"

    id = Column(BigInteger, primary_key=True)
    validator_requests_id = Column(
        BigInteger,
        ForeignKey("validator_requests.id"),
        nullable=False,
    )
    miner_uid = Column(Integer, nullable=False)  # deprecated
    miner_id = Column(
        BigInteger,
        ForeignKey("miners.id"),
        nullable=False,
    )
    prediction = Column(JSONB, nullable=False)
    bigtable_key = Column(Text, nullable=True)
    format_validation = Column(String, nullable=True)
    process_time = Column(Float, nullable=True)

    request = relationship("ValidatorRequest", back_populates="predictions")
    miner = relationship("Miner", back_populates="predictions")
    scores = relationship("MinerScore", back_populates="prediction")

    created_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class MinerScore(Base):
    __tablename__ = "miner_scores"

    id = Column(BigInteger, primary_key=True)
    miner_uid = Column(Integer, nullable=False)  # deprecated
    scored_time = Column(DateTime(timezone=True), nullable=False)
    miner_predictions_id = Column(
        BigInteger,
        ForeignKey("miner_predictions.id"),
        nullable=False,
    )
    prompt_score = Column(Float, nullable=False)
    prompt_score_v3 = Column(Float, nullable=False)
    score_details = Column(JSONB, nullable=False)
    score_details_v3 = Column(JSONB, nullable=False)

    prediction = relationship("MinerPrediction", back_populates="scores")


class MinerReward(Base):
    __tablename__ = "miner_rewards"

    id = Column(BigInteger, primary_key=True)
    miner_uid = Column(Integer, nullable=False)  # deprecated
    miner_id = Column(
        BigInteger,
        ForeignKey("miners.id"),
        nullable=False,
    )
    smoothed_score = Column(Float, nullable=False)
    reward_weight = Column(Float, nullable=False)
    prompt_name = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    miner = relationship("Miner", back_populates="rewards")


class MetagraphHistory(Base):
    __tablename__ = "metagraph_history"

    id = Column(BigInteger, primary_key=True)
    neuron_uid = Column(Integer, nullable=False)
    incentive = Column(Float, nullable=True)
    rank = Column(Float, nullable=True)
    stake = Column(Float, nullable=True)
    trust = Column(Float, nullable=True)
    emission = Column(Float, nullable=True)
    pruning_score = Column(Float, nullable=True)
    coldkey = Column(String, nullable=True)
    hotkey = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    ip_address = Column(String, nullable=True)


class WeightsUpdateHistory(Base):
    __tablename__ = "weights_update_history"

    id = Column(BigInteger, primary_key=True)
    miner_uids = Column(JSON, nullable=False)
    miner_weights = Column(JSON, nullable=False)
    norm_miner_uids = Column(JSON, nullable=False)
    norm_miner_weights = Column(JSON, nullable=False)
    update_result = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
