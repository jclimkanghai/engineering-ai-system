# Engineering AI package ownership and persisted records

The host (`engineering_ai_system` and application adapters) composes providers, Registry connections and user-facing routes. It resolves identity and selects provider implementations; it does not replace domain decisions with transport-specific rules.

`engineering_document_ai_brain` owns project planning, reasoning, integration and engineering assessment. `engineering_execution` owns bounded execution contracts and solvers. `engineering_ai_reviewer` owns independent, provider-neutral assurance and accepts a normalized `ReviewAnalyzer` request. `engineering_registry` owns persisted project state, evidence links, provenance, lifecycle and authority records. `pipelines` owns document ingestion, retrieval, application review assembly and presentation DTOs.

Finding payloads and extracted requirements produced by pipelines are proposals/DTOs. Accepted persisted state is represented by Registry `EngineeringIssue`, `FINDING`, `REQUIREMENT`, `EVIDENCE`, document/revision graph nodes and their audit/provenance records. A review approval is recorded separately and does not assert project acceptance or a human run-outcome decision.

For document review, `engineering_registry_path` selects the canonical document identity, revision, supersession and governing-status source. Human authority is read from Registry's existing evidence-backed `review_document_control` decisions; imported JSON status labels remain observations until that route confirms them. The JSON Document Registry remains an ingestion manifest/cache for extraction details such as OCR quality and Markdown paths. Legacy finding and document JSON are migration inputs or compatibility readers; their import commands default to dry-run and preserve their source files.

Provider construction belongs to the host. The standard workflow uses a host-owned lazy OpenAI adapter, while callers of `build_reasoning_workflow` must inject an adapter explicitly. Reviewer requests have one pipeline translation adapter (`ReviewerAnalysisAdapter`) before entering the normalized independent Reviewer contract.

`ProjectRunService` still uses Registry transactions directly for multi-record atomic writes. Read-only project-run access goes through public Registry service methods, which enforce project grants.
