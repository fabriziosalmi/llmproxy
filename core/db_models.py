from sqlalchemy import Column, String, Integer, Float, Text, MetaData, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()
metadata = Base.metadata

class Endpoint(Base):
    __tablename__ = 'endpoints'
    
    id = Column(String, primary_key=True)
    url = Column(String)
    status = Column(Integer)
    metadata_json = Column("metadata", Text)
    last_verified = Column(Text, nullable=True)
    latency_ms = Column(Float, nullable=True)
    success_rate = Column(Float, default=0.0)
    
    __table_args__ = (UniqueConstraint('url', name='uq_endpoint_url'),)

class AppState(Base):
    __tablename__ = 'app_state'
    
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
