"""
BaseAgent — abstract base for all investigation agents.

Provides:
  - Standardized invoke() contract with role injection and input validation
  - Automatic reasoning trace persistence
  - Automatic agent_run lifecycle management
  - Confidence score extraction from structured output
  - Graceful error handling that does not abort the workflow

Agents must implement:
  - agent_type: str class attribute
  - role: AgentRole class attribute (from agents.roles)
  - input_schema: Pydantic BaseModel subclass (from agents.schemas.inputs)
  - _extract_input(state) → dict  (fields to validate against input_schema)
  - _build_messages(state, case_memory, workflow_memory) → list[Message]
  - _parse_response(response) → (output_dict, confidence_score)
  - _state_update_key(output) → dict
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar, Type
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from agents.llm.base import LLMResponse, Message
from agents.llm.router import ModelRouter
from agents.memory.case import CaseMemory
from agents.memory.workflow import WorkflowMemory
from agents.persistence.traces import TraceWriter
from agents.roles import AgentRole
from agents.state import InvestigationState

logger = logging.getLogger(__name__)

_trace_writer = TraceWriter()


class BaseAgent(ABC):
    agent_type: str = "base"
    task_type: str = "default"              # maps to ModelRouter routing table
    role: ClassVar[AgentRole | None] = None
    input_schema: ClassVar[Type[BaseModel] | None] = None

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    def invoke(
        self,
        state: InvestigationState,
        session: Session,
        workflow_memory: WorkflowMemory,
        case_memory: CaseMemory,
        workflow_step: int = 0,
        parent_trace_id: UUID | None = None,
    ) -> dict:
        """
        Execute the agent and return a partial state update dict.
        Never raises — errors are captured and returned as state updates.
        """
        case_id = UUID(state["case_id"])
        session_id = UUID(state["session_id"])

        # Input validation — enforce structured contract before LLM call
        if self.input_schema is not None:
            validation_error = self._validate_input(state)
            if validation_error:
                logger.error(
                    "[%s] Input validation failed for case %s: %s",
                    self.agent_type, state["case_id"], validation_error,
                )
                return {
                    "errors": [{
                        "node":       self.agent_type,
                        "error":      f"Input validation failed: {validation_error}",
                        "error_type": "InputValidationError",
                    }],
                    "total_input_tokens":      0,
                    "total_output_tokens":     0,
                    "total_cache_read_tokens": 0,
                }

        # Create agent_run record
        agent_run_id = _trace_writer.create_agent_run(
            session,
            case_id=case_id,
            agent_type=self.agent_type,
            agent_name=self.__class__.__name__,
            input_payload={"case_id": state["case_id"], "node": self.agent_type},
            workflow_run_id=state["run_id"],
        )

        try:
            messages = self._build_messages(state, case_memory, workflow_memory)
            response = self._router.route(self.task_type, messages)
            output, confidence = self._parse_response(response)

            # Persist reasoning trace
            _trace_writer.write_reasoning_trace(
                session,
                session_id=session_id,
                case_id=case_id,
                agent_id=self.__class__.__name__,
                agent_type=self.agent_type,
                workflow_node=self.agent_type,
                workflow_step=workflow_step,
                parent_trace_id=parent_trace_id,
                input_context=self._build_input_context(state),
                response=response,
                confidence_score=confidence,
            )

            # Close agent_run
            _trace_writer.complete_agent_run(
                session,
                agent_run_id=agent_run_id,
                output_payload=output,
                model_id=response.model_id,
                token_usage={
                    "input_tokens":       response.input_tokens,
                    "output_tokens":      response.output_tokens,
                    "cache_read_tokens":  response.cache_read_tokens,
                    "cache_write_tokens": response.cache_write_tokens,
                    "latency_ms":         response.latency_ms,
                },
            )

            # Cache output in workflow memory
            workflow_memory.cache_output(self.agent_type, output)

            return {
                **self._state_update_key(output),
                "total_input_tokens":      response.input_tokens,
                "total_output_tokens":     response.output_tokens,
                "total_cache_read_tokens": response.cache_read_tokens,
            }

        except Exception as e:
            logger.exception("[%s] Agent failed for case %s", self.agent_type, state["case_id"])
            _trace_writer.fail_agent_run(session, agent_run_id, str(e))
            return {
                "errors": [{
                    "node":       self.agent_type,
                    "error":      str(e),
                    "error_type": type(e).__name__,
                }],
                "total_input_tokens":      0,
                "total_output_tokens":     0,
                "total_cache_read_tokens": 0,
            }

    @abstractmethod
    def _build_messages(
        self,
        state: InvestigationState,
        case_memory: CaseMemory,
        workflow_memory: WorkflowMemory,
    ) -> list[Message]:
        ...

    @abstractmethod
    def _parse_response(self, response: LLMResponse) -> tuple[dict, float | None]:
        """Returns (output_dict, confidence_score)."""
        ...

    @abstractmethod
    def _state_update_key(self, output: dict) -> dict:
        """Maps the output dict to the correct InvestigationState field."""
        ...

    def _extract_input(self, state: InvestigationState) -> dict:
        """
        Extract fields from state to validate against input_schema.
        Override in subclasses that declare an input_schema.
        Default implementation returns an empty dict (no-op validation).
        """
        return {}

    def _validate_input(self, state: InvestigationState) -> str | None:
        """
        Validates extracted state fields against input_schema.
        Returns an error message string on failure, None on success.
        """
        if self.input_schema is None:
            return None
        try:
            self.input_schema.model_validate(self._extract_input(state))
            return None
        except ValidationError as e:
            return str(e)

    def _build_input_context(self, state: InvestigationState) -> dict:
        """Compact context dict stored in reasoning_traces.input_context."""
        return {
            "case_id":        state["case_id"],
            "findings_count": len(state.get("findings", [])),
            "node":           self.agent_type,
            "role":           self.role.title if self.role else None,
        }
