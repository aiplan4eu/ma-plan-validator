from unified_planning.shortcuts import *
from unified_planning.io.ma_pddl_writer import MAPDDLWriter
from unified_planning.io.pddl_reader import PDDLReader
from unified_planning.plans.plan import ActionInstance
from unified_planning.plans.sequential_plan import SequentialPlan
from unified_planning.plans.partial_order_plan import PartialOrderPlan
from unified_planning.model import ProblemKind
from unified_planning.engines.meta_engine import MetaEngine, MetaEngineMeta
from unified_planning.engines.engine import Engine
from unified_planning.engines.mixins import PlanValidatorMixin
from unified_planning.engines.results import ValidationResult, ValidationResultStatus
from unified_planning.plans import PlanKind
from typing import Optional, cast
import unified_planning.engines.convert_mapddl_to_pddl as mapddl_to_pddl
from unified_planning.engines.plan_validator import SequentialPlanValidator
from unified_planning.engines.results import (
    ValidationResult,
    ValidationResultStatus,
    LogMessage,
    LogLevel,
    FailedValidationReason,
)
from unified_planning.exceptions import (
    UPConflictingEffectsException,
    UPUsageError,
    UPProblemDefinitionError,
    UPInvalidActionError,
)
import warnings
import os


class PlanConverter:
    def __init__(self, problem: up.model.AbstractProblem):
        self.problem = problem

    def convert_sequential_plan(self, sequential_plan: SequentialPlan):
        new_plan = []
        for act in sequential_plan.actions:
            new_act_instance = self._convert_action(act)
            new_plan.append(new_act_instance)
        return SequentialPlan(actions=new_plan, environment=self.problem.environment)

    def _convert_action(self, action_instance: ActionInstance):
        assert isinstance(action_instance, ActionInstance)
        if action_instance.agent is None:
            raise ValueError("Action instance does not have an associated agent.")
        action_name = f"{action_instance.action.name}_{action_instance.agent.name}"
        assert isinstance(
            self.problem, up.model.Problem
        ), "problem is not an instance of up.model.Problem"
        for act_prob in self.problem.actions:
            if action_name == act_prob.name:
                agent_param = self._get_agent_param(act_prob)
                new_params = [agent_param] + list(action_instance.actual_parameters)
                return ActionInstance(action=act_prob, params=new_params)
        raise ValueError(f"No matching action found for {action_name}")

    def _get_agent_param(self, act_prob: Action):
        assert isinstance(
            self.problem, up.model.Problem
        ), "problem is not an instance of up.model.Problem"
        agent_param = self.problem.object(act_prob.parameters[0].name)
        assert (
            agent_param.type == act_prob.parameters[0].type
        ), f"Type mismatch for agent parameter in action {act_prob.name}"
        return agent_param

    def convert_pop_plan(self, pop_plan: PartialOrderPlan):
        new_pop = pop_plan.replace_action_instances(self._convert_action)
        return new_pop


class MAPlanValidator(MetaEngine, PlanValidatorMixin):
    def __init__(self, engine_class, problem, *args, **kwargs):
        self._engine_class = engine_class
        self.problem = problem
        MetaEngine.__init__(self, *args, **kwargs)

    def _validate(
        self, problem: "up.model.AbstractProblem", plan: "up.plans.Plan"
    ) -> "ValidationResult":
        assert isinstance(problem, up.model.multi_agent.MultiAgentProblem)
        assert isinstance(plan, PartialOrderPlan)

        kind = problem.kind

        if not self.skip_checks and not self.supports(kind):
            msg = f"We cannot establish whether {self.name} can validate this problem!"
            if self.error_on_failed_checks:
                raise UPUsageError(msg)
            else:
                warnings.warn(msg)

        # Writing the MultiAgent problem
        ma_pddl_writer = MAPDDLWriter(problem, unfactored=True)
        domain_dir = f"ma_pddl_unfactored_{problem.name}"
        domain_file = f"{problem.name}_domain.pddl"
        problem_file = f"{problem.name}_problem.pddl"

        if not os.path.exists(domain_dir):
            os.makedirs(domain_dir)

        ma_pddl_writer.write_ma_domain(os.path.join(domain_dir, domain_file))
        ma_pddl_writer.write_ma_problem(os.path.join(domain_dir, problem_file))

        domain_path = os.path.join(domain_dir, domain_file)
        problem_path = os.path.join(domain_dir, problem_file)

        # Centralizing PDDL files
        centralized_dir = "centralized"
        os.makedirs(centralized_dir, exist_ok=True)

        new_domain_path = os.path.join(centralized_dir, domain_file)
        new_problem_path = os.path.join(centralized_dir, problem_file)

        # Convert the problem to a PDDL problem and write it out
        planning_problem = mapddl_to_pddl.PlanningProblem(domain_path, problem_path)
        planning_problem.write_pddl_domain(new_domain_path)
        planning_problem.write_pddl_problem(new_problem_path)

        # Parse the problem using the PDDL reader
        pddl_reader = PDDLReader()
        pddl_problem = pddl_reader.parse_problem(new_domain_path, new_problem_path)
        plan_converter = PlanConverter(pddl_problem)

        # Validate the plan
        is_valid_plan = True
        logs = []
        for seq_plan in plan.all_sequential_plans():
            new_seq_plan = plan_converter.convert_sequential_plan(seq_plan)
            with SequentialPlanValidator(problem_kind=pddl_problem.kind) as validator:
                validation_result = validator.validate(pddl_problem, new_seq_plan)
                if validation_result.status != ValidationResultStatus.VALID:
                    is_valid_plan = False
                    logs.append(LogMessage(LogLevel.INFO, "Invalid sequential plan"))
                    break

        if is_valid_plan:
            return ValidationResult(ValidationResultStatus.VALID, self.name(), logs)
        else:
            return ValidationResult(ValidationResultStatus.INVALID, self.name(), logs)

    def is_compatible_engine(self):
        return self._engine_class.is_oneshot_planner() and self.supports(
            ProblemKind({"ACTION_BASED_MULTI_AGENT"})
        )  # type: ignore

    def name(self):
        return "MAPlanValidator"

    @staticmethod
    def supports_plan(plan_kind: "PlanKind") -> bool:
        return plan_kind == PlanKind.PARTIAL_ORDER_PLAN

    @staticmethod
    def supported_kind() -> ProblemKind:
        supported_kind = ProblemKind()
        supported_kind.set_problem_class("ACTION_BASED_MULTI_AGENT")
        supported_kind.set_typing("FLAT_TYPING")
        supported_kind.set_typing("HIERARCHICAL_TYPING")
        supported_kind.set_parameters("BOOL_FLUENT_PARAMETERS")
        supported_kind.set_parameters("BOUNDED_INT_FLUENT_PARAMETERS")
        supported_kind.set_parameters("BOOL_ACTION_PARAMETERS")
        supported_kind.set_parameters("BOUNDED_INT_ACTION_PARAMETERS")
        supported_kind.set_parameters("UNBOUNDED_INT_ACTION_PARAMETERS")
        supported_kind.set_parameters("REAL_ACTION_PARAMETERS")
        supported_kind.set_numbers("DISCRETE_NUMBERS")
        supported_kind.set_numbers("BOUNDED_TYPES")
        supported_kind.set_problem_type("SIMPLE_NUMERIC_PLANNING")
        supported_kind.set_problem_type("GENERAL_NUMERIC_PLANNING")
        supported_kind.set_conditions_kind("NEGATIVE_CONDITIONS")
        supported_kind.set_conditions_kind("EQUALITIES")
        supported_kind.set_effects_kind("STATIC_FLUENTS_IN_BOOLEAN_ASSIGNMENTS")
        supported_kind.set_effects_kind("STATIC_FLUENTS_IN_NUMERIC_ASSIGNMENTS")
        supported_kind.set_effects_kind("STATIC_FLUENTS_IN_OBJECT_ASSIGNMENTS")
        supported_kind.set_effects_kind("FLUENTS_IN_BOOLEAN_ASSIGNMENTS")
        supported_kind.set_effects_kind("FLUENTS_IN_NUMERIC_ASSIGNMENTS")
        supported_kind.set_effects_kind("FLUENTS_IN_OBJECT_ASSIGNMENTS")
        supported_kind.set_effects_kind("DISJUNCTIVE_CONDITIONS")
        supported_kind.set_effects_kind("EXISTENTIAL_CONDITIONS")
        supported_kind.set_effects_kind("CONDITIONAL_EFFECTS")
        supported_kind.set_fluents_type("OBJECT_FLUENTS")
        supported_kind.set_quality_metrics("ACTIONS_COST")
        supported_kind.set_actions_cost_kind("STATIC_FLUENTS_IN_ACTIONS_COST")
        supported_kind.set_actions_cost_kind("FLUENTS_IN_ACTIONS_COST")
        supported_kind.set_quality_metrics("PLAN_LENGTH")
        supported_kind.set_quality_metrics("MAKESPAN")
        return supported_kind

    def _supports(self, problem_kind):
        return problem_kind <= MAPlanValidator._supported_kind(self._engine_class)

    def supports(self, problem_kind):
        return problem_kind <= MAPlanValidator._supported_kind(self._engine_class)

    @staticmethod
    def _supported_kind(engine) -> "ProblemKind":
        features = set(engine.supported_kind().features)
        supported_kind = ProblemKind(features)
        return supported_kind
