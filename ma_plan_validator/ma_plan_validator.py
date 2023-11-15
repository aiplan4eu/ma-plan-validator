import os
import warnings
import tempfile
import ma_plan_validator.convert_mapddl_to_pddl as mapddl_to_pddl
import unified_planning as up
from unified_planning.io.ma_pddl_writer import MAPDDLWriter
from unified_planning.io.pddl_reader import PDDLReader
from unified_planning.plans.plan import ActionInstance
from unified_planning.plans.sequential_plan import SequentialPlan
from unified_planning.plans.partial_order_plan import PartialOrderPlan
from unified_planning.model import ProblemKind, Action
from unified_planning.engines import Engine, MetaEngine
from unified_planning.engines.mixins import PlanValidatorMixin
from unified_planning.plans import PlanKind
from unified_planning.engines.plan_validator import SequentialPlanValidator
from unified_planning.engines.results import ValidationResult, ValidationResultStatus
from unified_planning.engines.results import LogMessage, LogLevel
from unified_planning.exceptions import UPUsageError
from typing import Type


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
                new_params = [agent_param]
                for ap in action_instance.actual_parameters:
                    new_obj = self.problem.object(ap.object().name)
                    new_params.append(new_obj)
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
    def __init__(self, *args, **kwargs):
        MetaEngine.__init__(self, *args, **kwargs)
        PlanValidatorMixin.__init__(self)

    def name(self):
        return "MAPlanValidator"

    @staticmethod
    def is_compatible_engine(engine: Type[Engine]) -> bool:
        if not engine.is_plan_validator():
            return False
        if not engine.supports_plan(PlanKind.PARTIAL_ORDER_PLAN) and not engine.supports_plan(PlanKind.SEQUENTIAL_PLAN): # type: ignore
            return False
        needed_kind = ProblemKind(version=2)
        needed_kind.set_typing("HIERARCHICAL_TYPING")
        if not engine.supports(needed_kind):
            return False
        return True

    @staticmethod
    def supports_plan(plan_kind: "PlanKind") -> bool:
        return plan_kind == PlanKind.PARTIAL_ORDER_PLAN or plan_kind == PlanKind.SEQUENTIAL_PLAN

    @staticmethod
    def _supported_kind(engine: Type[Engine]) -> "ProblemKind":
        supported_kind = ProblemKind(version=2)
        supported_kind.set_problem_class("ACTION_BASED_MULTI_AGENT")
        supported_kind.set_typing("FLAT_TYPING")
        supported_kind.set_typing("HIERARCHICAL_TYPING")
        supported_kind.set_conditions_kind("NEGATIVE_CONDITIONS")
        supported_kind.set_conditions_kind("EQUALITIES")
        supported_kind.set_conditions_kind("DISJUNCTIVE_CONDITIONS")
        supported_kind.set_conditions_kind("EXISTENTIAL_CONDITIONS")
        supported_kind.set_effects_kind("CONDITIONAL_EFFECTS")
        final_supported_kind = supported_kind.intersection(engine.supported_kind())
        additive_supported_kind = ProblemKind(version=2)
        additive_supported_kind.set_problem_class("ACTION_BASED_MULTI_AGENT")
        return final_supported_kind.union(additive_supported_kind)

    @staticmethod
    def _supports(problem_kind: "ProblemKind", engine: Type[Engine]) -> bool:
        return problem_kind <= MAPlanValidator._supported_kind(engine)

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

        with tempfile.TemporaryDirectory() as tempdir:
            # Writing the MultiAgent problem
            ma_pddl_writer = MAPDDLWriter(problem, unfactored=True)
            origin_dir = os.path.join(tempdir, "origin")
            ma_pddl_writer.write_ma_domain(origin_dir)
            ma_pddl_writer.write_ma_problem(origin_dir)
            domain_path = os.path.join(origin_dir, "domain.pddl")
            problem_path = os.path.join(origin_dir, "problem.pddl")

            # Centralizing PDDL files
            centralized_dir = os.path.join(tempdir, "centralized")
            os.makedirs(centralized_dir)
            new_domain_path = os.path.join(centralized_dir, "domain.pddl")
            new_problem_path = os.path.join(centralized_dir, "problem.pddl")

            # Convert the problem to a PDDL problem and write it out
            planning_problem = mapddl_to_pddl.PlanningProblem(domain_path, problem_path)
            planning_problem.write_pddl_domain(new_domain_path)
            planning_problem.write_pddl_problem(new_problem_path)

            # Parse the problem using the PDDL reader
            pddl_reader = PDDLReader()
            pddl_problem = pddl_reader.parse_problem(new_domain_path, new_problem_path)
            plan_converter = PlanConverter(pddl_problem)

        # Validate the plan
        logs = []
        if self.engine.supports_plan(PlanKind.PARTIAL_ORDER_PLAN):
            if plan.kind == PlanKind.PARTIAL_ORDER_PLAN:
                new_plan = plan_converter.convert_pop_plan(plan)
            else:
                new_plan = plan_converter.convert_pop_plan(plan.convert_to(PlanKind.PARTIAL_ORDER_PLAN))
            validation_result = self.engine.validate(pddl_problem, new_plan)
        else:
            if plan.kind == PlanKind.PARTIAL_ORDER_PLAN:
                for seq_plan in plan.all_sequential_plans():
                    new_plan = plan_converter.convert_sequential_plan(seq_plan)
                    validation_result = self.engine.validate(pddl_problem, new_plan)
                    if validation_result.status != ValidationResultStatus.VALID:
                        is_valid_plan = False
                        break
            else:
                new_plan = plan_converter.convert_sequential_plan(plan)
                validation_result = self.engine.validate(pddl_problem, new_plan)

        if validation_result.status != ValidationResultStatus.VALID:
            logs.append(LogMessage(LogLevel.INFO, "Invalid sequential plan"))
            return ValidationResult(ValidationResultStatus.INVALID, self.name(), logs)
        else:
            return ValidationResult(ValidationResultStatus.VALID, self.name(), logs)
