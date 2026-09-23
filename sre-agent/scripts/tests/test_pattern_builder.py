#!/usr/bin/env python3
"""pattern_builder.py unit tests."""

import os
import sys
import tempfile
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pattern_builder import PatternBuilder


@pytest.fixture
def output_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestRequiredFields:
    def test_save_fails_without_pattern(self, output_dir):
        pb = PatternBuilder("CG-1", 0, output_dir)
        assert pb.save() is False

    def test_save_succeeds_with_valid_pattern(self, output_dir):
        pb = PatternBuilder("CG-1", 0, output_dir)
        pb.set_pattern(
            pattern_id="hikaricp-pool-reduction",
            description="Reduce HikariCP max pool size",
            match_conditions=["root_cause.category == 'connection_pool_overload'"],
            execution_steps=["kubectl patch deployment ..."],
            risk="low",
        )
        assert pb.save() is True
        assert os.path.isfile(os.path.join(output_dir, "pattern_candidate.yaml"))


class TestValidation:
    def test_invalid_risk(self, output_dir):
        pb = PatternBuilder("CG-1", 0, output_dir)
        with pytest.raises(ValueError, match="risk"):
            pb.set_pattern(
                pattern_id="test",
                description="test",
                match_conditions=["a"],
                execution_steps=["b"],
                risk="INVALID",
            )

    def test_invalid_pattern_id_format(self, output_dir):
        pb = PatternBuilder("CG-1", 0, output_dir)
        with pytest.raises(ValueError, match="pattern_id"):
            pb.set_pattern(
                pattern_id="Has Spaces And CAPS",
                description="test",
                match_conditions=["a"],
                execution_steps=["b"],
                risk="low",
            )

    def test_empty_match_conditions(self, output_dir):
        pb = PatternBuilder("CG-1", 0, output_dir)
        pb.set_pattern(
            pattern_id="test-pattern",
            description="test",
            match_conditions=[],
            execution_steps=["b"],
            risk="low",
        )
        assert pb.save() is False

    def test_empty_execution_steps(self, output_dir):
        pb = PatternBuilder("CG-1", 0, output_dir)
        pb.set_pattern(
            pattern_id="test-pattern",
            description="test",
            match_conditions=["a"],
            execution_steps=[],
            risk="low",
        )
        assert pb.save() is False


class TestOutputStructure:
    def test_yaml_structure(self, output_dir):
        pb = PatternBuilder("CG-1", 0, output_dir)
        pb.set_pattern(
            pattern_id="hikaricp-pool-reduction",
            description="Reduce HikariCP max pool size when connections overloaded",
            match_conditions=["root_cause.category == 'connection_pool_overload'"],
            execution_steps=["kubectl patch deployment {name} -n {namespace} ..."],
            risk="low",
        )
        pb.save()

        with open(os.path.join(output_dir, "pattern_candidate.yaml")) as f:
            data = yaml.safe_load(f)

        assert data["cg_id"] == "CG-1"
        assert data["solution_idx"] == 0
        assert "created_at" in data
        assert data["pattern"]["pattern_id"] == "hikaricp-pool-reduction"
        assert data["pattern"]["risk"] == "low"
        assert len(data["pattern"]["match_conditions"]) == 1
        assert len(data["pattern"]["execution_steps"]) == 1
