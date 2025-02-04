# Copyright (c) conda-store development team. All rights reserved.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.

import asyncio
import datetime
import pathlib
import re
import sys
import tempfile
from unittest import mock

import pytest
import yarl
from celery.result import AsyncResult
from conda.base.context import context as conda_base_context
from constructor import construct
from fastapi.responses import RedirectResponse
from traitlets import TraitError

from conda_store_server import BuildKey, api
from conda_store_server._internal import action, conda_utils, orm, schema, server
from conda_store_server._internal.action import (
    generate_constructor_installer,
)
from conda_store_server.server.auth import DummyAuthentication


def test_action_decorator():
    """Test that the action decorator captures stdout/stderr and logs correctly."""

    @action.action
    def test_function(context):
        print("stdout")
        print("stderr", file=sys.stderr)
        if sys.platform == "win32":
            # echo is not a separate program on Windows
            context.run(["cmd", "/c", "echo subprocess"])
            context.run("echo subprocess_stdout", shell=True)
            context.run("echo subprocess_stderr>&2", shell=True)
            context.run(
                "echo subprocess_stderr_no_redirect>&2",
                shell=True,
                redirect_stderr=False,
            )
        else:
            context.run(["echo", "subprocess"])
            context.run("echo subprocess_stdout", shell=True)
            context.run("echo subprocess_stderr 1>&2", shell=True)
            context.run(
                "echo subprocess_stderr_no_redirect 1>&2",
                shell=True,
                redirect_stderr=False,
            )
        context.log.info("log")
        return pathlib.Path.cwd()

    context = test_function()

    stdout = context.stdout.getvalue()
    assert stdout.startswith(
        "stdout\nstderr\nsubprocess\nsubprocess_stdout\nsubprocess_stderr\nlog\n"
    )
    assert re.search(r"Action test_function completed in \d+\.\d+ s.\n$", stdout)

    assert context.stderr.getvalue() == "subprocess_stderr_no_redirect\n"
    # test that action direction is not the same as outside function
    assert context.result != pathlib.Path.cwd()
    # test that temporary directory is cleaned up
    assert not context.result.exists()


@pytest.mark.parametrize(
    "specification_name",
    [
        "simple_specification",
        "simple_specification_with_pip",
        "simple_lockfile_specification",
        "simple_lockfile_specification_with_pip",
    ],
)
def test_generate_constructor_installer(
    conda_store, specification_name, request, tmp_path
):
    """Test that generate_construction_installer correctly produces the files needed by `constructor`."""
    specification = request.getfixturevalue(specification_name)
    installer_dir = tmp_path / "installer_dir"
    is_lockfile = specification_name in [
        "simple_lockfile_specification",
        "simple_lockfile_specification_with_pip",
    ]

    # action_generate_constructor_installer uses a temporary directory context manager
    # to create and store the installer, but it usually gets deleted when the function
    # exits. Here, we manually create that temporary directory, run the action,
    # persisting the directory (so that we can verify the contents). Only then do we
    # manually clean up afterward.
    class PersistentTemporaryDirectory(tempfile.TemporaryDirectory):
        def __exit__(self, exc, value, tb):
            pass

    temp_directory = None

    def tmp_dir_side_effect(*args, **kwargs):
        nonlocal temp_directory
        temp_directory = PersistentTemporaryDirectory(*args, **kwargs)
        return temp_directory

    with mock.patch.object(
        generate_constructor_installer, "tempfile", wraps=tempfile
    ) as mock_tempfile:
        mock_tempfile.TemporaryDirectory.side_effect = tmp_dir_side_effect

        # Create the installer, but don't actually run `constructor` - it uses conda to solve the
        # environment, which we don't need to do for the purposes of this test.
        with mock.patch(
            "conda_store_server._internal.action.generate_constructor_installer.logged_command"
        ) as mock_command:
            generate_constructor_installer.action_generate_constructor_installer(
                conda_command=conda_store.conda_command,
                specification=specification,
                installer_dir=installer_dir,
                version="1",
                is_lockfile=is_lockfile,
            )

    mock_command.assert_called()

    # First call to `constructor` is used to check that it is installed
    mock_command.call_args_list[0].args[1] == ["constructor", "--help"]

    # Second call is used to build the installer
    call_args = mock_command.call_args_list[1].args[1]
    cache_dir = pathlib.Path(call_args[3])
    platform = call_args[5]
    tmp_dir = pathlib.Path(call_args[6])
    assert call_args[0:3] == ["constructor", "-v", "--cache-dir"]
    assert str(cache_dir).endswith("pkgs")
    assert call_args[4:6] == ["--platform", conda_base_context.subdir]
    assert str(tmp_dir).endswith("build")

    # Use some of the constructor internals to verify the action's artifacts are valid
    # constructor input
    info = construct.parse(str(tmp_dir / "construct.yaml"), platform)
    construct.verify(info)

    assert temp_directory is not None
    temp_directory.cleanup()


def test_fetch_and_extract_conda_packages(tmp_path, simple_conda_lock):
    context = action.action_fetch_and_extract_conda_packages(
        conda_lock_spec=simple_conda_lock,
        pkgs_dir=tmp_path,
    )

    assert context.stdout.getvalue()


@pytest.mark.long_running_test
def test_install_specification(tmp_path, conda_store, simple_specification):
    conda_prefix = tmp_path / "test"

    action.action_install_specification(
        conda_command=conda_store.conda_command,
        specification=simple_specification,
        conda_prefix=conda_prefix,
    )

    assert conda_utils.is_conda_prefix(conda_prefix)


def test_install_lockfile(tmp_path, conda_store, simple_conda_lock):
    conda_prefix = tmp_path / "test"

    action.action_install_lockfile(
        conda_lock_spec=simple_conda_lock, conda_prefix=conda_prefix
    )

    assert conda_utils.is_conda_prefix(conda_prefix)


@pytest.mark.long_running_test
def test_generate_conda_export(conda_store, conda_prefix):
    context = action.action_generate_conda_export(
        conda_command=conda_store.conda_command, conda_prefix=conda_prefix
    )
    # The env name won't be correct because conda only sets the env name when
    # an environment is in an envs dir. See the discussion on PR #549.
    context.result["name"] = "test-prefix"

    schema.CondaSpecification.model_validate(context.result)


@pytest.mark.long_running_test
def test_generate_conda_pack(tmp_path, conda_prefix):
    output_filename = tmp_path / "environment.tar.gz"

    action.action_generate_conda_pack(
        conda_prefix=conda_prefix,
        output_filename=output_filename,
    )

    assert output_filename.exists()


def test_remove_not_conda_prefix(tmp_path):
    fake_conda_prefix = tmp_path / "test"
    fake_conda_prefix.mkdir()

    with pytest.raises(ValueError):
        action.action_remove_conda_prefix(fake_conda_prefix)


def test_remove_conda_prefix(tmp_path, simple_conda_lock):
    conda_prefix = tmp_path / "test"

    action.action_install_lockfile(
        conda_lock_spec=simple_conda_lock, conda_prefix=conda_prefix
    )

    assert conda_utils.is_conda_prefix(conda_prefix)

    action.action_remove_conda_prefix(conda_prefix)

    assert not conda_utils.is_conda_prefix(conda_prefix)
    assert not conda_prefix.exists()


@pytest.mark.skipif(
    sys.platform == "win32", reason="permissions are not supported on Windows"
)
def test_set_conda_prefix_permissions(tmp_path, conda_store, simple_conda_lock):
    conda_prefix = tmp_path / "test"

    action.action_install_lockfile(
        conda_lock_spec=simple_conda_lock, conda_prefix=conda_prefix
    )

    context = action.action_set_conda_prefix_permissions(
        conda_prefix=conda_prefix,
        permissions="755",
        uid=None,
        gid=None,
    )
    assert "no changes for permissions of conda_prefix" in context.stdout.getvalue()
    assert "no changes for gid and uid of conda_prefix" in context.stdout.getvalue()


def test_get_conda_prefix_stats(tmp_path, conda_store, simple_conda_lock):
    conda_prefix = tmp_path / "test"

    action.action_install_lockfile(
        conda_lock_spec=simple_conda_lock, conda_prefix=conda_prefix
    )

    context = action.action_get_conda_prefix_stats(conda_prefix)
    assert context.result["disk_usage"] > 0


@pytest.mark.long_running_test
def test_add_conda_prefix_packages(db, conda_store, simple_specification, conda_prefix):
    build_id = conda_store.register_environment(
        db, specification=simple_specification, namespace="pytest"
    )

    action.action_add_conda_prefix_packages(
        db=db,
        conda_prefix=conda_prefix,
        build_id=build_id,
    )

    build = api.get_build(db, build_id=build_id)
    assert len(build.package_builds) > 0


@pytest.mark.long_running_test
def test_add_lockfile_packages(
    db,
    conda_store,
    simple_specification,
    simple_conda_lock,
    celery_worker,
):
    task, solve_id = conda_store.register_solve(db, specification=simple_specification)

    action.action_add_lockfile_packages(
        db=db,
        conda_lock_spec=simple_conda_lock,
        solve_id=solve_id,
    )

    solve = api.get_solve(db, solve_id=solve_id)
    assert len(solve.package_builds) > 0

    result = AsyncResult(task)
    result.get(timeout=30)
    assert result.state == "SUCCESS"


@pytest.mark.parametrize(
    "is_legacy_build, build_key_version",
    [
        (False, 0),  # invalid
        (False, 1),  # long (legacy)
        (False, 2),  # shorter hash (default)
        (False, 3),  # hash-only (experimental)
        (True, 1),  # build_key_version doesn't matter because there's no lockfile
    ],
)
@pytest.mark.long_running_test
def test_api_get_build_lockfile(
    request,
    conda_store,
    db,
    simple_specification_with_pip,
    conda_prefix,
    is_legacy_build,
    build_key_version,
):
    # sets build_key_version
    if build_key_version == 0:  # invalid
        with pytest.raises(
            TraitError,
            match=(
                r"c.CondaStore.build_key_version: invalid build key version: 0, "
                r"expected: \(1, 2, 3\)"
            ),
        ):
            conda_store.build_key_version = build_key_version
        return  # invalid, nothing more to test
    conda_store.build_key_version = build_key_version
    assert BuildKey.current_version() == build_key_version
    assert BuildKey.versions() == (1, 2, 3)

    # initializes data needed to get the lockfile
    specification = simple_specification_with_pip
    specification.name = "this-is-a-long-environment-name"
    namespace = "pytest"

    class MyAuthentication(DummyAuthentication):
        # Skips auth (used in api_get_build_lockfile). Test version of request
        # has no state attr, which is returned in the real impl of this method.
        # So I have to overwrite the method itself.
        def authorize_request(self, *args, **kwargs):
            pass

    auth = MyAuthentication()
    build_id = conda_store.register_environment(
        db, specification=specification, namespace=namespace
    )
    db.commit()
    build = api.get_build(db, build_id=build_id)
    # makes this more visible in the lockfile
    build_id = 12345678
    build.id = build_id
    # makes sure the timestamp in build_key is always the same
    build.scheduled_on = datetime.datetime(2023, 11, 5, 3, 54, 10, 510258)
    environment = api.get_environment(db, namespace=namespace)

    # adds packages (returned in the lockfile)
    action.action_add_conda_prefix_packages(
        db=db,
        conda_prefix=conda_prefix,
        build_id=build_id,
    )

    key = "" if is_legacy_build else build.conda_lock_key

    # creates build artifacts
    build_artifact = orm.BuildArtifact(
        build_id=build_id,
        build=build,
        artifact_type=schema.BuildArtifactType.LOCKFILE,
        key=key,  # key value determines returned lockfile type
    )
    db.add(build_artifact)
    db.commit()

    # gets lockfile for this build
    res = asyncio.run(
        server.views.api.api_get_build_lockfile(
            request=request,
            conda_store=conda_store,
            auth=auth,
            namespace=namespace,
            environment_name=environment.name,
            build_id=build_id,
        )
    )

    if key == "":
        # legacy build: returns pinned package list
        lines = res.split("\n")
        assert len(lines) > 2
        assert lines[:2] == [
            f"#platform: {conda_utils.conda_platform()}",
            "@EXPLICIT",
        ]
        assert re.match("http.*//.*tar.bz2#.*", lines[2]) is not None
    else:
        # new build: redirects to lockfile generated by conda-lock
        def lockfile_url(build_key):
            return f"lockfile/{build_key}.yml"

        if build_key_version == 1:
            build_key = (
                "c7afdeffbe2bda7d16ca69beecc8bebeb29280a95d4f3ed92849e4047710923b-"
                "20231105-035410-510258-12345678-this-is-a-long-environment-name"
            )
        elif build_key_version == 2:
            build_key = "c7afdeff-1699156450-12345678-this-is-a-long-environment-name"
        elif build_key_version == 3:
            build_key = "c1f206a26263e1166e5b43548f69aa0c"
        else:
            raise ValueError(f"unexpected build_key_version: {build_key_version}")
        assert type(res) is RedirectResponse
        assert key == res.headers["location"]
        assert build.build_key == build_key
        assert BuildKey.get_build_key(build) == build_key
        assert build.parse_build_key(conda_store, build_key) == 12345678
        assert BuildKey.parse_build_key(conda_store, build_key) == 12345678
        assert lockfile_url(build_key) == build.conda_lock_key
        assert lockfile_url(build_key) == res.headers["location"]
        assert res.status_code == 307


@pytest.mark.long_running_test
def test_api_get_build_installer(
    request, conda_store, db, simple_specification_with_pip, conda_prefix
):
    # initializes data needed to get the installer
    specification = simple_specification_with_pip
    specification.name = "my-env"
    namespace = "pytest"

    class MyAuthentication(DummyAuthentication):
        # Skips auth (used in api_get_build_installer). Test version of request
        # has no state attr, which is returned in the real impl of this method.
        # So I have to overwrite the method itself.
        def authorize_request(self, *args, **kwargs):
            pass

    auth = MyAuthentication()
    build_id = conda_store.register_environment(
        db, specification=specification, namespace=namespace
    )
    db.commit()

    build = api.get_build(db, build_id=build_id)

    # creates build artifacts
    build_artifact = orm.BuildArtifact(
        build_id=build_id,
        build=build,
        artifact_type=schema.BuildArtifactType.CONSTRUCTOR_INSTALLER,
        key=build.constructor_installer_key,
    )
    db.add(build_artifact)
    db.commit()

    # gets installer for this build
    res = asyncio.run(
        server.views.api.api_get_build_installer(
            request=request,
            conda_store=conda_store,
            auth=auth,
            build_id=build_id,
        )
    )

    # redirects to installer
    def installer_url(build_key):
        ext = "exe" if sys.platform == "win32" else "sh"
        return f"installer/{build_key}.{ext}"

    assert type(res) is RedirectResponse
    assert build.constructor_installer_key == res.headers["location"]
    assert installer_url(build.build_key) == build.constructor_installer_key
    assert res.status_code == 307


def test_get_channel_url():
    conda_main = "https://conda.anaconda.org/main"
    repo_main = "https://repo.anaconda.com/pkgs/main"
    example = "https://example.com"

    assert conda_utils.get_channel_url(conda_main) == yarl.URL(repo_main)
    assert conda_utils.get_channel_url(f"{conda_main}/") == yarl.URL(repo_main)
    assert conda_utils.get_channel_url(example) == yarl.URL(example)
