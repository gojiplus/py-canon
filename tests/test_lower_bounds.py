"""Tests for py_canon.lower_bounds, one per case the 1.6.0 review found."""

from py_canon.lower_bounds import environment, group_requirements, minimums


def _pins(deps=(), **extras):
    return minimums(
        {
            "project": {
                "dependencies": list(deps),
                "optional-dependencies": {k: list(v) for k, v in extras.items()},
            }
        }
    )


def test_a_plain_lower_bound_is_pinned():
    assert _pins(["numpy>=2.1", "pandas>=2.2.3,<3"]) == ["numpy==2.1", "pandas==2.2.3"]


def test_base_and_extra_merge_to_the_highest_minimum():
    assert _pins(["a>=1"], fast=["a>=2"]) == ["a==2"]


def test_a_minimum_the_specifier_excludes_is_skipped():
    assert _pins(["b>=1,!=1"]) == []


def test_strict_and_wildcard_and_url_are_skipped():
    assert _pins(["a>1", "c==4.*", "d @ https://example.org/d.whl"]) == []


def test_extras_on_the_requirement_are_kept():
    assert _pins(["g[http]>=2"]) == ["g[http]==2"]


def test_extras_merge_across_groups():
    assert _pins(["g[http]>=2"], s3=["g[s3]>=2"]) == ["g[http,s3]==2"]


def test_extra_marker_is_evaluated_with_its_group_name():
    assert _pins(optional=["h>=1; extra == 'optional'"]) == ["h==1"]


def test_a_false_marker_is_skipped():
    assert _pins(["w>=1; sys_platform == 'nonexistent'"]) == []


def test_compatible_and_exact_pins():
    assert _pins(["e~=2.1", "f==3.2"]) == ["e==2.1", "f==3.2"]


def test_no_dependencies_gives_no_pins():
    assert minimums({"project": {}}) == []


def test_a_constraint_without_a_minimum_can_exclude_the_pin():
    assert _pins(["idna>=2.4"], x=["idna!=2.4"]) == []


def test_extras_from_a_bare_requirement_are_kept():
    assert _pins(["g>=2"], http=["g[http]"]) == ["g[http]==2"]


def test_markers_use_the_target_python():
    deps = {"project": {"dependencies": ["idna>=2.5; python_version < '3.13'"]}}
    assert minimums(deps, "3.12.9") == ["idna==2.5"]
    assert minimums(deps, "3.13.1") == []


def test_groups_expand_include_group_once():
    data = {
        "dependency-groups": {
            "dev": ["ruff", {"include-group": "test"}],
            "test": ["pytest>=8", "ruff"],
        }
    }
    assert group_requirements(data) == ["ruff", "pytest>=8"]


def test_environment_lists_pins_tools_and_the_package_with_extras():
    data = {
        "project": {
            "dependencies": ["numpy>=2.1"],
            "optional-dependencies": {"io": ["pyarrow>=18"], "web": []},
        },
        "dependency-groups": {"test": ["pytest>=8"]},
    }
    assert environment(data) == [
        "numpy==2.1",
        "pyarrow==18",
        "pytest>=8",
        "-e .[io,web]",
    ]


def test_normalized_group_includes():
    groups = {"Dev": [{"include-group": "Test_Tools"}], "test-tools": ["pytest-cov"]}
    assert group_requirements({"dependency-groups": groups}) == ["pytest-cov"]


def test_conditional_pytest_does_not_hide_fallback():
    groups = {"test": ["pytest; python_version < '3.12'"]}
    assert "pytest" in environment({"dependency-groups": groups}, "3.13.1")
