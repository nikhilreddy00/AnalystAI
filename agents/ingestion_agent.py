"""
Data Ingestion Agent — CrewAI agent that:
1. Calls the appropriate connector to load data into a DataFrame
2. Uses Groq (Llama 3.3-70B) to enrich the schema inference with business context
3. Returns a DataContract that all downstream agents consume

Why Groq here: schema inference is fast + structured — Llama 3.3-70B on Groq
handles "cust_id is an identifier, revenue_usd is a metric" in <1s.
No need to burn Claude tokens on this mechanical task.
"""

from __future__ import annotations


from crewai import Agent, Crew, LLM, Task
from crewai.process import Process

from api.config import settings
from connectors.base import get_connector
from orchestrator.state import AnalysisSession, DataContract


_groq_llm = LLM(
    model="groq/llama-3.3-70b-versatile",
    api_key=settings.groq_api_key,
    temperature=0.0,
)


def _make_agent() -> Agent:
    return Agent(
        role="Data Ingestion Specialist",
        goal=(
            "Connect to the specified data source, load the data, and produce a "
            "complete schema description that business and technical users can understand."
        ),
        backstory=(
            "You are a data engineer who has connected to hundreds of data sources. "
            "You know how to detect column types, identify key business metrics, "
            "date columns, and identifier columns just by looking at column names and samples."
        ),
        llm=_groq_llm,
        verbose=False,
        allow_delegation=False,
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

    # Enrich with Groq LLM: confirm metric/date/id classifications and add context
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
    """Ask Groq to produce a richer, business-aware schema summary."""
    col_descriptions = "\n".join(
        f"- {c.name} ({c.dtype}): sample={c.sample_values[:3]}, null%={c.null_pct}"
        for c in contract.columns
    )
    task_context = ""
    if task_plan:
        task_context = f"The analyst is trying to answer: {task_plan.raw_query}"

    agent = _make_agent()
    task = Task(
        description=f"""
Given this dataset schema, write a 2-3 sentence business-readable summary that explains:
- What this dataset represents
- Which columns are the key business metrics
- The date range / granularity if detectable from column names and samples
- Any obvious data quality concerns (high null%, mixed types)

{task_context}

COLUMNS:
{col_descriptions}

Return ONLY the plain text summary, no bullet points, no headers.
""",
        expected_output="A 2-3 sentence plain text schema summary",
        agent=agent,
    )

    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = await crew.kickoff_async()
    return str(result).strip()
