ALTER TABLE pe_scenarios
ADD COLUMN evidence_basis_rule_id TEXT NULL
CHECK (evidence_basis_rule_id IS NULL OR evidence_basis_rule_id = 'VAL-03');
