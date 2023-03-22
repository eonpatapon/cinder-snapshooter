"""This module destroys the expired automatic snapshots

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
import datetime
import sys

import structlog

from openstack.exceptions import ResourceNotFound
from tenacity import Retrying, TryAgain, stop_after_delay, wait_random

from .utils import run_on_all_projects


log = structlog.get_logger()
cmd_help = "Destroys expired snapshots"


def process_snapshots(os_client, wait_completion_timeout, dry_run):
    """Delete every expired snapshot"""
    destroyed_snapshot = 0
    errors = 0
    for snapshot in os_client.block_storage.snapshots(status="available"):
        try:
            log.debug(
                "Looking at snapshot",
                snapshot=snapshot.id,
                project=os_client.current_project_id,
            )
            if "expire_at" not in snapshot.metadata:
                continue
            expire_at = datetime.datetime.combine(
                date=datetime.date.fromisoformat(snapshot.metadata["expire_at"]),
                time=datetime.time.min,
                tzinfo=datetime.timezone.utc,
            )
            now = datetime.datetime.now(datetime.timezone.utc)
            if now > expire_at:
                log.debug(
                    "Deleting snapshot",
                    snapshot=snapshot.id,
                    project=os_client.current_project_id,
                    expire_at=expire_at.isoformat(),
                )
                if not dry_run:
                    os_client.block_storage.delete_snapshot(snapshot)
                    for attempt in Retrying(
                        wait=wait_random(min=1, max=2),
                        stop=stop_after_delay(wait_completion_timeout),
                    ):
                        with attempt:
                            check_snapshot_deletion(os_client, snapshot.id)
                            log.info(
                                "Deleted snapshot",
                                snapshot=snapshot.id,
                                project=os_client.current_project_id,
                                expire_at=expire_at.isoformat(),
                            )
                            destroyed_snapshot += 1
            else:
                log.debug(
                    "Keeping snapshot, still valid",
                    snapshot=snapshot.id,
                    expire_at=expire_at.isoformat(),
                )
        except Exception:
            log.exception("Failed to delete snapshot", snapshot=snapshot.id)
            errors += 1
    log.info(
        "Processed all snapshots in project",
        destroyed_snapshot=destroyed_snapshot,
        errors=errors,
        project=os_client.current_project_id,
    )
    return errors == 0


def check_snapshot_deletion(os_client, snapshot_id: str):
    try:
        os_client.block_storage.get_snapshot(snapshot_id)
    except ResourceNotFound:
        return True
    raise TryAgain


def register_args(parser):
    """Registers subcommand specific arguments to parse using argparse"""
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Do not create any snapshot, only pretend to",
    )


def cli(args):
    """Entrypoint for CLI subcommand"""

    if not all(
        run_on_all_projects(
            args.os_client,
            process_snapshots,
            args.pool_size,
            args.wait_completion_timeout,
            args.dry_run,
        )
    ):
        sys.exit(1)  # Something went wrong during execution exit with 1
