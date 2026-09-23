"""Studio data model — the DanDon Media content production pipeline.

Everything the tracker shows lives in these tables and every row is editable
from the UI.

  StudioProject     — anything we're working on. kind = series | episode |
                      documentary | project (generic). Episodes hang off a
                      series via parent_id. `rank` orders siblings (drag to
                      reorder); `priority` is the P1–P4 label.
  StudioSegment     — one Stewart-Doctrine segment of an episode (or one act
                      of a documentary). Each gets its own writer task.
  StudioTask        — a unit of work in a project's pipeline, assigned to a
                      specialist. `status` is the board column, `rank` the
                      order inside the column.
  StudioSpecialist  — the model roster: who does which kind of work, with an
                      editable system prompt + model id.
  StudioMessage     — conversation log (intake interview, Spock sessions).
  StudioSource      — substantiating documents. Feeds the visuals desk and the
                      public transparency page.
  StudioAsset       — produced artifacts: scripts, audio, visuals, animation
                      briefs, music cues, render manifests, renders.
  StudioPost        — a per-platform publication via OnlySocial.
  StudioSetting     — key/value JSON settings (platforms, links bar, brand).
"""
import secrets
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, JSON,
)
from sqlalchemy.orm import relationship

from ..models import Base, now_utc


PROJECT_KINDS = ["series", "episode", "documentary", "project"]
PROJECT_STATUSES = ["intake", "investigating", "planning", "in_production",
                    "review", "rendered", "published", "parked", "killed"]
PRIORITIES = ["P1", "P2", "P3", "P4"]
TASK_STATUSES = ["todo", "running", "review", "done", "blocked"]


def _public_id():
    return secrets.token_urlsafe(8)


class StudioProject(Base):
    __tablename__ = "studio_projects"

    id = Column(Integer, primary_key=True)
    public_id = Column(String(32), unique=True, index=True, default=_public_id)
    kind = Column(String(20), nullable=False, default="project")
    parent_id = Column(Integer, ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(300), nullable=False)
    premise = Column(Text, default="")
    status = Column(String(30), nullable=False, default="intake")
    priority = Column(String(4), nullable=False, default="P2")
    rank = Column(Float, nullable=False, default=0.0, index=True)
    brief = Column(JSON, default=dict)      # intake answers {question: answer}
    data = Column(JSON, default=dict)       # topics, verdict, master script, runtime...
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    parent = relationship("StudioProject", remote_side=[id], backref="children")
    tasks = relationship("StudioTask", back_populates="project", cascade="all, delete-orphan",
                         order_by="StudioTask.rank")
    segments = relationship("StudioSegment", back_populates="project", cascade="all, delete-orphan",
                            order_by="StudioSegment.rank")
    sources = relationship("StudioSource", back_populates="project", cascade="all, delete-orphan",
                           order_by="StudioSource.rank")
    assets = relationship("StudioAsset", back_populates="project", cascade="all, delete-orphan",
                          order_by="StudioAsset.rank")
    messages = relationship("StudioMessage", back_populates="project", cascade="all, delete-orphan",
                            order_by="StudioMessage.id")
    posts = relationship("StudioPost", back_populates="project", cascade="all, delete-orphan",
                         order_by="StudioPost.id")


class StudioSegment(Base):
    __tablename__ = "studio_segments"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    rank = Column(Float, nullable=False, default=0.0)
    title = Column(String(300), nullable=False)
    format = Column(String(40), default="deep_dive")  # cold_open|headline|deep_dive|correspondent|interview|closer|act
    minutes = Column(Float, default=2.0)
    angle = Column(Text, default="")
    beats = Column(JSON, default=list)
    source_urls = Column(JSON, default=list)
    writer = Column(String(60), default="segment_writer")
    script = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=now_utc)

    project = relationship("StudioProject", back_populates="segments")


class StudioTask(Base):
    __tablename__ = "studio_tasks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    segment_id = Column(Integer, ForeignKey("studio_segments.id", ondelete="CASCADE"), nullable=True)
    stage = Column(String(40), nullable=False, default="custom")
    title = Column(String(300), nullable=False)
    instructions = Column(Text, default="")
    specialist = Column(String(60), nullable=True)
    status = Column(String(20), nullable=False, default="todo")
    rank = Column(Float, nullable=False, default=0.0)
    auto = Column(Boolean, default=True)    # can a specialist model run it?
    output = Column(Text, default="")
    output_data = Column(JSON, default=dict)
    error = Column(Text, default="")
    model_used = Column(String(80), default="")
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    project = relationship("StudioProject", back_populates="tasks")
    segment = relationship("StudioSegment")


class StudioSpecialist(Base):
    __tablename__ = "studio_specialists"

    key = Column(String(60), primary_key=True)
    name = Column(String(120), nullable=False)
    role = Column(String(200), default="")
    description = Column(Text, default="")
    model = Column(String(80), default="")
    effort = Column(String(10), default="high")
    tools = Column(JSON, default=list)      # ["web_search", "web_fetch"]
    system_prompt = Column(Text, default="")
    enabled = Column(Boolean, default=True)
    rank = Column(Float, default=0.0)


class StudioMessage(Base):
    __tablename__ = "studio_messages"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    channel = Column(String(30), default="showrunner")
    role = Column(String(20), nullable=False)   # user | assistant
    specialist = Column(String(60), default="")
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    project = relationship("StudioProject", back_populates="messages")


class StudioSource(Base):
    __tablename__ = "studio_sources"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    rank = Column(Float, default=0.0)
    url = Column(String(2000), default="")
    title = Column(String(500), default="")
    publisher = Column(String(300), default="")
    kind = Column(String(40), default="article")  # bill|court_filing|gov_report|dataset|transcript|video|article|other
    published = Column(String(40), default="")
    quote = Column(Text, default="")
    supports = Column(Text, default="")            # which claim this substantiates
    verified = Column(Boolean, default=False)       # a human has opened it and confirmed
    added_by = Column(String(60), default="")
    created_at = Column(DateTime(timezone=True), default=now_utc)

    project = relationship("StudioProject", back_populates="sources")


class StudioAsset(Base):
    __tablename__ = "studio_assets"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    segment_id = Column(Integer, ForeignKey("studio_segments.id", ondelete="SET NULL"), nullable=True)
    source_id = Column(Integer, ForeignKey("studio_sources.id", ondelete="SET NULL"), nullable=True)
    kind = Column(String(30), nullable=False)  # script_stage|script_clean|audio|visual|animation|music|manifest|render|transparency
    title = Column(String(300), default="")
    url = Column(String(2000), default="")
    path = Column(String(1000), default="")
    body = Column(Text, default="")
    meta = Column(JSON, default=dict)
    status = Column(String(20), default="draft")   # draft|needs_sourcing|ready|approved
    rank = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    project = relationship("StudioProject", back_populates="assets")
    source = relationship("StudioSource")


class StudioPost(Base):
    __tablename__ = "studio_posts"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(40), nullable=False)
    title = Column(String(300), default="")
    caption = Column(Text, default="")
    hashtags = Column(String(500), default="")
    status = Column(String(20), default="draft")   # draft|approved|sent|failed
    scheduled_for = Column(String(40), default="")  # "YYYY-MM-DD HH:MM" or "" = now
    external_id = Column(String(200), default="")
    response = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_utc)

    project = relationship("StudioProject", back_populates="posts")


class StudioSetting(Base):
    __tablename__ = "studio_settings"

    key = Column(String(80), primary_key=True)
    value = Column(JSON)
