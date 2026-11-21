from pathlib import Path

import pytest

from video_tool.crop.keyframes import Keyframe, Project


def test_save_cannot_overwrite_source_or_unrelated_file(tmp_path):
    source = tmp_path / "capture.mp4"
    source.write_bytes(b"original recording")
    alias = tmp_path / "source-hardlink.json"
    alias.hardlink_to(source)
    symlink = tmp_path / "source-symlink.json"
    symlink.symlink_to(source)
    unrelated = tmp_path / "other.json"
    unrelated.write_text('{"unrelated": true}', encoding="utf-8")
    project = Project(source.name)

    for path in (source, alias, symlink, unrelated):
        with pytest.raises(ValueError, match="Refusing to overwrite"):
            project.save(path)

    assert source.read_bytes() == b"original recording"
    assert unrelated.read_text(encoding="utf-8") == '{"unrelated": true}'


def test_project_can_replace_its_previous_version(tmp_path):
    path = tmp_path / "project.json"
    project = Project("capture.mp4")
    project.save(path)
    project.keyframes = [Keyframe(0, 0.25)]
    project.save(path)
    saved = Project.load(path)
    assert saved.keyframes == project.keyframes
    assert Path(saved.source) == (tmp_path / "capture.mp4").resolve()


def test_save_captures_source_metadata_when_missing(tmp_path, pq_source):
    path = tmp_path / "project.json"
    Project(pq_source.name).save(path)
    snapshot = Project.load(path).source_metadata
    assert snapshot["hdr_format"] == "HDR10"
    assert snapshot["metadata"]["sampled_frames"]
