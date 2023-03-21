"""
Copyright 2021 Gandi SAS

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import datetime
import sys

import pytest

from openstack.exceptions import ResourceNotFound

import cinder_snapshooter.snapshot_destroyer

from fixtures import FakeSnapshot


@pytest.mark.parametrize("success", [True, False])
def test_cli(mocker, faker, success):
    mocker.patch("cinder_snapshooter.snapshot_destroyer.run_on_all_projects")
    mocker.patch("sys.exit")
    cinder_snapshooter.snapshot_destroyer.run_on_all_projects.return_value = [
        True,
        True,
        success,
    ]
    fake_args = argparse.Namespace(
        dry_run=faker.boolean(),
        os_client=mocker.MagicMock(),
        pool_size=10,
    )
    cinder_snapshooter.snapshot_destroyer.cli(fake_args)
    cinder_snapshooter.snapshot_destroyer.run_on_all_projects.assert_called_once_with(
        fake_args.os_client,
        cinder_snapshooter.snapshot_destroyer.process_snapshots,
        fake_args.pool_size,
        fake_args.dry_run,
    )
    if not success:
        sys.exit.assert_called_once_with(1)


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real-run"])
@pytest.mark.parametrize("success", [True, False])
def test_process_snapshots(mocker, faker, log, time_machine, dry_run, success):
    os_client = mocker.MagicMock()
    manual_snapshots = [
        FakeSnapshot(
            id=faker.uuid4(),
            status="available",
            created_at=faker.date_time_this_century(
                tzinfo=datetime.timezone.utc
            ).isoformat(),
            volume_id=faker.uuid4(),
            metadata={},
        )
        for i in range(10)
    ]

    now = faker.date_time_this_century(tzinfo=datetime.timezone.utc)
    time_machine.move_to(now)
    expired_snapshot = [
        FakeSnapshot(
            id=faker.uuid4(),
            status="available",
            created_at=faker.date_time_this_century(
                tzinfo=datetime.timezone.utc
            ).isoformat(),
            volume_id=faker.uuid4(),
            metadata={"expire_at": faker.date(end_datetime=now)},
        )
        for i in range(10)
    ]
    nok_snapshot = []
    nok_snapshot_deletion = []
    if not success:
        nok_snapshot = [
            FakeSnapshot(
                id=faker.uuid4(),
                status="available",
                created_at=faker.date_time_this_century(
                    tzinfo=datetime.timezone.utc
                ).isoformat(),
                volume_id=faker.uuid4(),
                metadata={"expire_at": faker.date(end_datetime=now)},
            )
            for i in range(10)
        ]
        nok_snapshot_deletion = [
            FakeSnapshot(
                id=faker.uuid4(),
                status="available",
                created_at=faker.date_time_this_century(
                    tzinfo=datetime.timezone.utc
                ).isoformat(),
                volume_id=faker.uuid4(),
                metadata={"expire_at": faker.date(end_datetime=now)},
            )
        ]
    not_expired_snapshot = [
        FakeSnapshot(
            id=faker.uuid4(),
            status="available",
            created_at=faker.date_time_this_century(
                tzinfo=datetime.timezone.utc
            ).isoformat(),
            volume_id=faker.uuid4(),
            metadata={
                "expire_at": faker.date_this_century(
                    before_today=False, after_today=True
                ).isoformat()
            },
        )
    ]
    snapshots = (
        manual_snapshots
        + not_expired_snapshot
        + expired_snapshot
        + nok_snapshot
        + nok_snapshot_deletion
    )
    os_client.block_storage.snapshots.return_value = snapshots

    def delete_snapshot(isnapshot):
        if isnapshot in nok_snapshot:
            raise Exception()
        return 1

    os_client.block_storage.delete_snapshot.side_effect = delete_snapshot

    def get_snapshot(isnapshot):
        if isnapshot in [s.id for s in nok_snapshot_deletion]:
            return mocker.MagicMock()
        raise ResourceNotFound()

    os_client.block_storage.get_snapshot.side_effect = get_snapshot

    assert (
        cinder_snapshooter.snapshot_destroyer.process_snapshots(os_client, dry_run)
        == success
        or dry_run
    )

    os_client.block_storage.snapshots.assert_called_once_with(status="available")
    if dry_run:
        os_client.block_storage.delete_snapshot.assert_not_called()
        return

    assert os_client.block_storage.delete_snapshot.call_count == len(
        expired_snapshot
    ) + len(nok_snapshot) + len(nok_snapshot_deletion)
    for snapshot in expired_snapshot + nok_snapshot + nok_snapshot_deletion:
        os_client.block_storage.delete_snapshot.assert_any_call(snapshot)
    assert log.has(
        "Processed all snapshots in project",
        destroyed_snapshot=len(expired_snapshot),
        errors=len(nok_snapshot) + len(nok_snapshot_deletion),
        project=os_client.current_project_id,
    )
