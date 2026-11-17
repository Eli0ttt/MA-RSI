# Meta model mutation

Choose exactly one modification based on the measured public task result: model_id assignments among the listed models, or the DAG topology (agent positions, roles and reports_to edges). A prompt may be updated only for an agent whose role changes in the topology edit. New agents start with openai:deepseek-v4-flash-s2 and receive the environment's research tools. Keep the official task interface. Return the entire YAML and an evidence-based rationale.

Current configuration:
```yaml
{{mas_config}}
```

Execution logs: {{execution_logs}}
Observations: {{observations}}

Return a fenced json decision with observed_evidence, root_cause_hypothesis, component, intended_change, predicted_effect, predicted_direction, followed by a fenced yaml block with the full configuration.

{{model_constraint}}
{{memory_context}}
