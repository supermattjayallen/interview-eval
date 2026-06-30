from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    normalized_recording_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    recording_url: Mapped[str] = mapped_column(Text, nullable=False)
    role_title: Mapped[str | None] = mapped_column(Text)
    role_description: Mapped[str | None] = mapped_column(Text)
    interview_step: Mapped[str | None] = mapped_column(String(64), index=True)
    interview_step_inferred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    transcript_summary: Mapped[str | None] = mapped_column(Text)
    evaluation_skipped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    topics_covered: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
        order_by="Question.question_index",
    )

    __table_args__ = (Index("ix_recordings_step_analyzed", "interview_step", "analyzed_at"),)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False)
    question_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_normalized: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    question_timestamp: Mapped[str | None] = mapped_column(String(32))
    candidate_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer_timestamp: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    quality: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[int | None] = mapped_column(Integer)
    strengths: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    gaps: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    recording: Mapped["Recording"] = relationship(back_populates="questions")
    sample_answer: Mapped["SampleAnswer | None"] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint("recording_id", "question_index", name="uq_questions_recording_index"),
        Index("ix_questions_category_step", "category", "recording_id"),
    )


class SampleAnswer(Base):
    __tablename__ = "sample_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    ideal_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ideal_answer_points: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="analysis")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    question: Mapped["Question"] = relationship(back_populates="sample_answer")


class PrepQuestion(Base):
    """Polished, prep-ready questions derived from raw transcript extractions."""

    __tablename__ = "prep_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_normalized: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False, index=True)
    original_question: Mapped[str] = mapped_column(Text, nullable=False)
    display_question: Mapped[str] = mapped_column(Text, nullable=False)
    interview_step: Mapped[str | None] = mapped_column(String(64), index=True)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    avg_score: Mapped[float | None] = mapped_column()
    topics: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    interview_steps: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    why_likely: Mapped[str | None] = mapped_column(Text)
    preparation_tips: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    strong_answer_outline: Mapped[str | None] = mapped_column(Text)
    based_on_role: Mapped[str | None] = mapped_column(Text)
    source_question_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    polished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_prep_questions_step_category", "interview_step", "category"),)
