"""S9 real Git fixture gate definitions; never run during S6 construction.

All mutating Git setup is confined to a fresh owned temporary repository, with
empty template/profile/config and no provider, actual user repo or credentials.
Unsupported mandatory format is a gate failure, not a silently green skip.
"""

import asyncio
import os

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.service import RunService
from doppel_agent.workspace.git_authorization import select_metadata_root
from doppel_agent.workspace.git_reader import GitInspector, _git_executable
from doppel_agent.workspace.process_supervisor import ProcessSupervisor


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize("ref_format", ["files", "reftable"])
def test_real_owned_repository_and_linked_worktree_status_diff_without_source_config(tmp_path, object_format, ref_format):
    async def scenario():
        repository = tmp_path / "owned-repository"
        repository.mkdir()
        profile, template = tmp_path / "empty-profile", tmp_path / "empty-template"
        profile.mkdir()
        template.mkdir()
        executable, _ = _git_executable(repository, None)
        supervisor = ProcessSupervisor()
        env = {}
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            value = os.environ.get(name)
            if value is not None:
                env[name] = value
        env.update({"HOME": str(profile), "XDG_CONFIG_HOME": str(profile), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1", "LC_ALL": "C"})

        async def git(*argv):
            result = await supervisor.run_binary(
                [str(executable), "--no-pager", "-c", f"core.hooksPath={template}",
                 "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", "-c", "gc.auto=0",
                 "-c", "maintenance.auto=false", "-c", "commit.gpgSign=false",
                 "-c", "user.name=Owned Fixture", "-c", "user.email=owned-fixture@example.invalid", *argv],
                cwd=repository, run_id="owned-git-fixture-setup", env=env,
                output_limit=1024 * 1024, timeout_seconds=30, require_tree_ownership=True,
            )
            assert result.exit_code == 0, f"mandatory isolated Git format/setup gate failed: {argv[0]}"
            return result.stdout

        try:
            await git("init", "--initial-branch=main", f"--template={template}",
                      f"--object-format={object_format}", f"--ref-format={ref_format}")
            (repository / "source.py").write_bytes(b"base\n")
            (repository / ".env").write_bytes(b"fixture only, never an actual credential\n")
            await git("add", "--", "source.py", ".env")
            await git("commit", "-qm", "owned fixture")
            linked = tmp_path / "owned-linked-worktree"
            await git("worktree", "add", "--detach", str(linked))
            (repository / "source.py").write_bytes(b"index\n")
            await git("add", "--", "source.py")
            (repository / "source.py").write_bytes(b"working\n")
            (linked / "source.py").write_bytes(b"linked working\n")
            bad_include = tmp_path / "invalid-include-fixture"
            bad_include.write_text("[invalid configuration fixture\n", encoding="ascii")
            config = repository / ".git" / "config"
            with config.open("a", encoding="utf-8") as handle:
                handle.write(f'\n[include]\n\tpath = "{bad_include.as_posix()}"\n')
            # No original-repository Git setup operation after poisoning config.
            inspector = GitInspector(repository, supervisor=supervisor, executable=executable)
            status = await inspector.status()
            assert status["available"], status
            assert status["object_format"] == object_format and status["ref_storage"] == ref_format
            row = next(row for row in status["rows"] if row["path"] == "source.py")
            assert row["staged"] == "modified" and row["worktree"] == "different_raw"
            assert all(row["path"] != ".env" for row in status["rows"])
            assert status["repository_clean"] is None and status["excluded_count"] > 0
            staged = await inspector.diff("source.py", plane="staged", expected_fingerprint=status["fingerprint"])
            assert staged["available"] and "-base\n" in staged["text"] and "+index\n" in staged["text"]
            working = await inspector.diff("source.py")
            assert working["available"] and "-index\n" in working["text"] and "+working\n" in working["text"]
            combined = await inspector.diff("source.py", plane="combined")
            assert combined["available"] and "-base\n" in combined["text"] and "+working\n" in combined["text"]
            unauth = GitInspector(linked, supervisor=supervisor, executable=executable)
            assert (await unauth.status())["reason"] == "git_metadata_authorization_required"
            owned = GitInspector(linked, supervisor=supervisor, executable=executable,
                                 authorized_metadata_roots=(repository / ".git",))
            linked_status = await owned.status()
            assert linked_status["available"] and linked_status["linked_worktree"], linked_status
            linked_diff = await owned.diff("source.py")
            assert linked_diff["available"] and "+linked working\n" in linked_diff["text"]
            # Product owned path, not merely direct reader roots. Native dialogs
            # remain a separate required S9 UI gate; this only exercises a trusted
            # synthetic owner selection and real Git through original service.
            service = RunService(linked, provider=MockProvider())
            try:
                assert (await service.git_status())["reason"] == "git_metadata_authorization_required"
                grant = await service._grant_git_metadata(select_metadata_root(repository / ".git", workspace=linked))
                assert grant["active"] and not grant["persistent"]
                product_status = await service.git_status()
                assert product_status["available"] and product_status["linked_worktree"]
                product_diff = await service.git_diff("source.py", expected_fingerprint=product_status["fingerprint"])
                assert product_diff["available"] and "+linked working\n" in product_diff["text"]
                assert not (await service._revoke_git_metadata())["active"]
                assert (await service.git_status())["reason"] == "git_metadata_authorization_required"
                assert service.process_supervisor.active_count == 0
            finally:
                await service.close()
            assert (repository / "source.py").read_bytes() == b"working\n"
            assert (linked / "source.py").read_bytes() == b"linked working\n"
            assert bad_include.read_text(encoding="ascii") == "[invalid configuration fixture\n"
            assert supervisor.active_count == 0
        finally:
            await supervisor.close()

    asyncio.run(scenario())
