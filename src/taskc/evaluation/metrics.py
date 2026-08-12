from __future__ import annotations

from dataclasses import dataclass, field

from taskc.api import TaskCompiler
from taskc.models import TaskCompilerError

from .dataset import EvaluationScenario


@dataclass(slots=True)
class EvaluationReport:
    scenario_count: int = 0
    status_matches: int = 0
    critical_gap_hits: int = 0
    critical_gap_total: int = 0
    false_ready_count: int = 0
    forbid_ready_total: int = 0
    candidate_recall_hits: int = 0
    candidate_recall_total: int = 0
    duplicate_questions: int = 0
    question_count: int = 0
    clarification_turns_total: int = 0
    clarification_scenarios: int = 0
    contract_violations_rejected: int = 0
    contract_violation_total: int = 0
    failures: list[str] = field(default_factory=list)

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    @property
    def critical_gap_recall(self) -> float:
        return self._ratio(self.critical_gap_hits, self.critical_gap_total)

    @property
    def false_ready_rate(self) -> float:
        return self._ratio(self.false_ready_count, self.forbid_ready_total)

    @property
    def candidate_recall_at_5(self) -> float:
        return self._ratio(self.candidate_recall_hits, self.candidate_recall_total)

    @property
    def duplicate_question_rate(self) -> float:
        return self._ratio(self.duplicate_questions, self.question_count)

    @property
    def average_clarification_turns(self) -> float:
        return self._ratio(self.clarification_turns_total, self.clarification_scenarios)

    @property
    def contract_violation_rejection_rate(self) -> float:
        return self._ratio(
            self.contract_violations_rejected, self.contract_violation_total
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "scenario_count": self.scenario_count,
            "status_matches": self.status_matches,
            "critical_gap_recall": self.critical_gap_recall,
            "critical_gap_numerator": self.critical_gap_hits,
            "critical_gap_denominator": self.critical_gap_total,
            "false_ready_rate": self.false_ready_rate,
            "false_ready_numerator": self.false_ready_count,
            "false_ready_denominator": self.forbid_ready_total,
            "candidate_recall_at_5": self.candidate_recall_at_5,
            "candidate_recall_numerator": self.candidate_recall_hits,
            "candidate_recall_denominator": self.candidate_recall_total,
            "duplicate_question_rate": self.duplicate_question_rate,
            "duplicate_question_numerator": self.duplicate_questions,
            "duplicate_question_denominator": self.question_count,
            "average_clarification_turns": self.average_clarification_turns,
            "clarification_turns_numerator": self.clarification_turns_total,
            "clarification_turns_denominator": self.clarification_scenarios,
            "contract_violation_rejection_rate": self.contract_violation_rejection_rate,
            "contract_violation_numerator": self.contract_violations_rejected,
            "contract_violation_denominator": self.contract_violation_total,
            "failures": self.failures,
        }


def evaluate_scenarios(
    scenarios: list[EvaluationScenario],
    compiler_factory,
) -> EvaluationReport:
    report = EvaluationReport(scenario_count=len(scenarios))
    for scenario in scenarios:
        compiler: TaskCompiler = compiler_factory()
        if scenario.expected_error_category is not None:
            report.contract_violation_total += 1
        try:
            result = compiler.compile(scenario.request, scenario.context)
        except TaskCompilerError as exc:
            if exc.error.category == scenario.expected_error_category:
                report.contract_violations_rejected += 1
            else:
                report.failures.append(
                    f"{scenario.id}: error {exc.error.category}, expected "
                    f"{scenario.expected_error_category or 'a domain result'}"
                )
            continue
        initial_result = result
        answered_targets: set[str] = set()
        observed_question_ids: set[str] = set()

        def observe_questions() -> None:
            for observed in result.questions:
                if observed.id in observed_question_ids:
                    continue
                observed_question_ids.add(observed.id)
                report.question_count += 1
                report.duplicate_questions += int(
                    bool(set(observed.targets) & answered_targets)
                )

        observe_questions()
        turns = 0
        for scripted in scenario.answer_script:
            target = scripted.get("target")
            question = next(
                (
                    item for item in result.questions
                    if target is None or target in item.targets
                ),
                None,
            )
            if question is None:
                report.failures.append(
                    f"{scenario.id}: answer script target is not currently askable: {target}"
                )
                break
            for question_target in question.targets:
                answered_targets.add(question_target)
            answer = {
                "question_id": question.id,
                "result_id": question.result_id,
                "revision": question.revision,
            }
            if "value_ref" in scripted:
                answer["value_ref"] = scripted["value_ref"]
            else:
                answer["value"] = scripted.get("value")
            result = compiler.continue_(
                compiler.last_session_id,
                result.revision,
                [answer],
            )
            turns += 1
            observe_questions()
        if scenario.answer_script:
            report.clarification_scenarios += 1
            report.clarification_turns_total += turns
        if result.status == scenario.expected_status:
            report.status_matches += 1
        else:
            report.failures.append(
                f"{scenario.id}: status {result.status}, expected {scenario.expected_status}"
            )
        actual_targets = {
            target for gap in initial_result.gaps for target in gap.field_paths
        }
        expected_targets = set(scenario.required_gap_targets)
        report.critical_gap_hits += len(actual_targets & expected_targets)
        report.critical_gap_total += len(expected_targets)
        if scenario.forbid_ready:
            report.forbid_ready_total += 1
            report.false_ready_count += int(result.status == "ready")
        if scenario.expected_capabilities:
            report.candidate_recall_total += 1
            actual = {
                f"{candidate.capability_id}@{candidate.capability_version}"
                for candidate in initial_result.candidates[:5]
            }
            report.candidate_recall_hits += int(bool(actual & set(scenario.expected_capabilities)))
    return report


__all__ = ["EvaluationReport", "evaluate_scenarios"]
