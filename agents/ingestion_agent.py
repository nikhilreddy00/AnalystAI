"""
Data Ingestion Agent:
1. Calls the appropriate connector to load data into a DataFrame
2. Asks a fast-tier Claude model for a business-readable schema summary
3. Returns a DataContract that all downstream agents consume

Phase 1: the summary call moved from Groq/CrewAI onto the shared LLM client (ADR-0005)
after Groq stopped serving llama-3.3-70b-versatile, which broke every ingestion.
"""

from __future__ import annotations

from api.config import settings
from connectors.base import get_connector
from llm.client import get_client
from orchestrator.state import AnalysisSession, DataContract

# The former CrewAI agent's role/goal/backstory, kept as the system prompt.
_SYSTEM = (
    "You are a Data Ingestion Specialist: a data engineer who has connected to hundreds of "
    "data sources. You know how to detect column types, identify key business metrics, date "
    "columns, and identifier columns just by looking at column names and samples. Your goal: "
    "produce a complete schema description that business and technical users can understand."
)


async def run_ingestion_agent(session: AnalysisSession) -> DataContract:
    """
    Load data from the configured connector and return an enriched DataContract.
    This is called by the LangGraph orchestrator at the 'ingestion' node.
    """
    config = session.connector_config
    if not config:
        raise ValueError("No connector config in session")

    connector = get_connector(config)

    # Load the raw DataFrame
    df = await connector.load()

    # Build the base DataContract from structural analysis
    contract = connector.build_data_contract(df)

    # Enrich with an LLM summary: business context for the downstream agents
    enriched_summary = await _enrich_schema_with_llm(contract, session.task_plan)

    contract.schema_summary = enriched_summary

    # Store the DataFrame on the session for downstream agents
    # We serialize via parquet bytes so it can be passed to the E2B sandbox
    import io
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()

    return contract


async def _enrich_schema_with_llm(contract: DataContract, task_plan) -> str:
    """Ask the fast-tier model for a richer, business-aware schema summary."""
    col_descriptions = "\n".join(
        f"- {c.name} ({c.dtype}): sample={c.sample_values[:3]}, null%={c.null_pct}"
        for c in contract.columns
    )
    task_context = ""
    if task_plan:
        task_context = f"The analyst is trying to answer: {task_plan.raw_query}"

    response = await get_client().complete(
        agent="ingestion", prompt_version="v0", model=settings.llm_fast_model,
        max_tokens=400, system=_SYSTEM,
        messages=[{"role": "user", "content": f"""
Given this dataset schema, write a 2-3 sentence business-readable summary that explains:
- What this dataset represents
- Which columns are the key business metrics
- The date range / granularity if detectable from column names and samples
- Any obvious data quality concerns (high null%, mixed types)

{task_context}

COLUMNS:
{col_descriptions}

Return ONLY the plain text summary, no bullet points, no headers.
"""}],
    )
    return response.text.strip()
