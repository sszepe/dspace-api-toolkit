# -*- coding: UTF-8 -*-
"""
Review process (configurable workflow) for DSpace API (DSpace 7+ / DSpace-CRIS)

A deposit into a collection with workflow groups becomes a workflow item.
Each step (reviewstep, editstep, finaleditstep by default) offers a *pool
task* to the members of the step's group; a member *claims* it (claimed
task) and performs an option: approve, reject (back to the submitter with a
reason), edit metadata, ... Supervision orders give a group access to an
in-progress submission.

    GET|DELETE /workflow/workflowitems[/{id}]              DELETE: back to workspace, ?expunge=true: delete
    GET    /workflow/workflowitems/{id}/item|collection|submissionDefinition|step
    GET    /workflow/workflowitems/search/findBySubmitter?uuid=   /search/item?uuid=
    PATCH  /workflow/workflowitems/{id}                    section PATCH (edit_metadata option)
    GET    /workflow/pooltasks/{id}[/workflowitem|eperson|group|step|action]
    GET    /workflow/pooltasks/search/findByUser|findAllByItem|findByItem?uuid=
    POST   /workflow/claimedtasks                          text/uri-list: claim a pool task
    GET    /workflow/claimedtasks/{id}[/workflowitem|owner|step|action]
    GET    /workflow/claimedtasks/search/findByUser|findAllByItem|findByItem?uuid=
    POST   /workflow/claimedtasks/{id}                     form data: submit_approve, submit_reject&reason, ...
    DELETE /workflow/claimedtasks/{id}                     unclaim (back to the pool)
    GET    /config/workflowdefinitions[/{name}[/collections|/steps]]   /search/findByCollection?uuid=
    GET    /config/workflowsteps/{name}   /config/workflowactions/{name}
    GET|POST|DELETE /core/supervisionorders[/{id}]         /search/byItem?uuid=

    wf = client.workflow
    for task in wf.pending_tasks():                       # what is waiting for me
        print(task["kind"], task["title"], task["step"], task["options"])
    wf.review(item_uuid, "approve")                       # claim if needed, then approve
    wf.review(item_uuid, "reject", reason="Please add an abstract")
    wf.approve_all_steps(item_uuid)                       # walk through every step (bulk approval)

Claiming needs membership in the step's group (collection role
workflowGroups/<role>); being an administrator alone is not enough.
"""

import logging
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from ._base import BaseManager
from .exceptions import DSpaceError
from .metadata import MetadataPatch, as_operations
from .projection import EmbedArg

# Logger for the module
logger = logging.getLogger(__name__)

# Workflow API endpoints and related constants
WORKFLOW_ITEMS = "/workflow/workflowitems"
POOL_TASKS = "/workflow/pooltasks"
CLAIMED_TASKS = "/workflow/claimedtasks"
DEFINITIONS = "/config/workflowdefinitions"
STEPS = "/config/workflowsteps"
ACTIONS = "/config/workflowactions"
SUPERVISION_ORDERS = "/core/supervisionorders"

# Links and embed constants for workflow items and tasks
POOL_TASK_LINKS = ("workflowitem", "eperson", "group", "step", "action")
CLAIMED_TASK_LINKS = ("workflowitem", "owner", "step", "action")
WORKFLOW_ITEM_LINKS = ("item", "collection", "submissionDefinition", "step")
SUPERVISION_TYPES = ("NONE", "EDITOR", "OBSERVER")
# tasks come with their workflow item and its item (title, uuid) in one request
TASK_EMBED = ["workflowitem", "workflowitem/item"]

# Type alias for references to workflow items, tasks, or other objects
Ref = Union[str, int, Dict[str, Any]]


class WorkflowError(DSpaceError):
    """A review action could not be performed (no task, not authorized, validation)."""


def submit_param(option: str) -> str:
    """Option name -> request parameter: "approve" -> "submit_approve" (already prefixed names are kept).
    
    Parameters:
        option: The option name to convert to a request parameter

    Returns:
        str: The corresponding request parameter name
    """
    return option if option.startswith("submit_") else f"submit_{option}"


class WorkflowManager(BaseManager):
    """Workflow items, pool / claimed tasks, workflow configuration and supervision orders"""

    def __init__(self, auth):
        """Initialize the workflow manager with the given authentication context.
        
        Parameters:
            auth: The authentication context to use for API requests

        """
        super().__init__(auth)
        self._epersons = None
        self._action_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def epersons(self):
        """Get the EPerson manager, initializing it if necessary.
        
        Returns:
            EPersonManager: The EPerson manager instance

        """
        if self._epersons is None:
            from .epersons import EPersonManager
            self._epersons = EPersonManager(self.auth)
        return self._epersons

    def _id_of(self, ref: Ref) -> str:
        """Get the ID of the given reference, which can be a string, integer, or dictionary containing an 'id' or 'uuid'.
        
        Parameters:
            ref: The reference to get the ID from

        Returns:
            str: The ID of the object

        """
        if isinstance(ref, dict):
            ident = ref.get("id", ref.get("uuid"))
            if ident is None:
                raise ValueError(f"Object has no id: {ref!r}")
            return str(ident)
        return str(ref)

    def _uuid(self, ref: Ref, what: str = "object") -> str:
        """Get the UUID of the given reference, which can be a dictionary containing a 'uuid' or an integer (not allowed).
        
        Parameters:
            ref: The reference to get the UUID from
            what: A description of the object type for error messages

        Returns:
            str: The UUID of the object

        """
        uuid = self._uuid_of(ref) if not isinstance(ref, int) else None
        if not uuid:
            raise ValueError(f"Not a {what} UUID or dict: {ref!r}")
        return uuid

    def _user_uuid(self, eperson: Optional[Ref]) -> str:
        """Get the UUID of the given EPerson reference, or the current user if None is provided.
        
        Parameters:
            eperson: The EPerson reference, or None to use the current user

        Returns:
            str: The UUID of the EPerson

        """
        if eperson is not None:
            return self._uuid(eperson, "eperson")
        me = self.epersons.get_current_user()
        if not me:
            raise WorkflowError("Not logged in - pass an eperson")
        return me["uuid"]

    # ==================================================================
    # Workflow items
    # ==================================================================

    def iter_workflow_items(self, page_size: Optional[int] = None, embed: EmbedArg = None) -> Iterator[Dict[str, Any]]:
        """All workflow items (administrators). A generator.

        Parameters:
            page_size: The number of items per page for pagination
            embed: The embed parameter to include related entities in the response

        Yields:
            Dict[str, Any]: A workflow item dictionary

        """
        return self._iter_embedded(self._url(WORKFLOW_ITEMS), "workflowitems", "List workflow items",
                                   page_size=page_size, embed=embed)

    def get_workflow_item(self, workflow_item: Ref, embed: EmbedArg = None) -> Dict[str, Any]:
        """Retrieve a workflow item by its reference.

        Parameters:
            workflow_item: The reference to the workflow item
            embed: The embed parameter to include related entities in the response

        Returns:
            Dict[str, Any]: A workflow item dictionary

        """

        ident = self._id_of(workflow_item)
        return self._get_json(self._url(WORKFLOW_ITEMS, f"/{ident}"), f"Get workflow item {ident}", embed=embed)

    def get_workflow_item_link(self, workflow_item: Ref, link: str, all_languages: Optional[bool] = None
                               ) -> Optional[Dict[str, Any]]:
        """Linked entity: "item", "collection", "submissionDefinition" or "step".
        
        Parameters:
            workflow_item: The reference to the workflow item
            link: The linked entity to retrieve ("item", "collection", "submissionDefinition" or "step")
            all_languages: Whether to include all language variants

        Returns:
            Optional[Dict[str, Any]]: The linked entity dictionary, or None if not found

        """
        if link not in WORKFLOW_ITEM_LINKS:
            raise ValueError(f"link must be one of {WORKFLOW_ITEM_LINKS}")
        ident = self._id_of(workflow_item)
        return self._get_optional(self._url(WORKFLOW_ITEMS, f"/{ident}/{link}"),
                                  f"Get {link} of workflow item {ident}", all_languages=all_languages)

    def find_by_submitter(self, eperson: Ref, embed: EmbedArg = None) -> List[Dict[str, Any]]:
        """Find workflow items by the submitter.

        Parameters:
            eperson: The reference to the submitter (EPerson)
            embed: The embed parameter to include related entities in the response

        Returns:
            List[Dict[str, Any]]: A list of workflow item dictionaries

        """
        uuid = self._uuid(eperson, "eperson")
        return list(self._iter_embedded(self._url(WORKFLOW_ITEMS, "/search/findBySubmitter"), "workflowitems",
                                        f"Workflow items of submitter {uuid}", [("uuid", uuid)], embed=embed))

    def find_by_item(self, item: Ref, embed: EmbedArg = None) -> Optional[Dict[str, Any]]:
        """The workflow item of an item, or None (not in workflow).

        Parameters:
            item: The reference to the item
            embed: The embed parameter to include related entities in the response

        Returns:
            Optional[Dict[str, Any]]: The workflow item dictionary, or None if not found

        """
        uuid = self._uuid(item, "item")
        return self._get_optional(self._url(WORKFLOW_ITEMS, "/search/item"), f"Find workflow item of {uuid}",
                                  params={"uuid": uuid}, embed=embed)

    def return_to_workspace(self, workflow_item: Ref) -> bool:
        """Stop the workflow and send the submission back to the submitter's workspace (administrators)."""
        ident = self._id_of(workflow_item)
        self._request("DELETE", self._url(WORKFLOW_ITEMS, f"/{ident}"), f"Return workflow item {ident} to workspace",
                      expected=(204,))
        logger.info("Workflow item %s returned to the workspace", ident)
        return True

    def delete_workflow_item(self, workflow_item: Ref) -> bool:
        """Delete the submission while in workflow (?expunge=true, administrators).

        Parameters:
            workflow_item: The reference to the workflow item to delete

        Returns:
            bool: True if the workflow item was successfully deleted

        """
        ident = self._id_of(workflow_item)
        self._request("DELETE", self._url(WORKFLOW_ITEMS, f"/{ident}"), f"Delete workflow item {ident}",
                      expected=(204,), params={"expunge": "true"})
        logger.info("Workflow item %s deleted", ident)
        return True

    def patch_workflow_item(self, workflow_item: Ref,
                            operations: Union[MetadataPatch, Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        PATCH the sections of a workflow item (needs a claimed task whose
        action offers "edit_metadata"). Same operations as for workspace items.

        Parameters:
            workflow_item: The reference to the workflow item to patch
            operations: The metadata patch operations to apply

        Returns:
            Dict[str, Any]: The updated workflow item dictionary

        """
        ops = as_operations(operations)
        if not ops:
            raise ValueError("Empty patch")
        ident = self._id_of(workflow_item)
        return self._request("PATCH", self._url(WORKFLOW_ITEMS, f"/{ident}"), f"Patch workflow item {ident}",
                             headers=self._headers("application/json-patch+json"), json=ops).json()

    def patch_section_metadata(self, workflow_item: Ref, section: str,
                               metadata: Union[MetadataPatch, Dict[str, Any]]) -> Dict[str, Any]:
        """Patch the metadata of a specific section of a workflow item.

        The metadata can be provided as a dictionary of {field: values} (each replaces the field) or a MetadataPatch.for_section(section).

        Parameters:
            workflow_item: The reference to the workflow item to patch
            section: The section of the workflow item to patch
            metadata: The metadata patch or dictionary of field values

        Returns:
            Dict[str, Any]: The updated workflow item dictionary

        """
        if isinstance(metadata, dict):
            patch = MetadataPatch.for_section(section)
            for field, values in metadata.items():
                patch.set(field, values)
            metadata = patch
        return self.patch_workflow_item(workflow_item, metadata)

    # ==================================================================
    # Tasks
    # ==================================================================

    def _task_search(self, base: str, key: str, method: str, uuid: str) -> List[Dict[str, Any]]:
        """Search for tasks based on a specific method and UUID.

        Parameters:
            base: The base URL for the task type
            key: The key to identify the tasks in the response
            method: The search method to use
            uuid: The UUID to search for

        Returns:
            List[Dict[str, Any]]: A list of task dictionaries

        """
        return list(self._iter_embedded(self._url(base, f"/search/{method}"), key, f"{key} {method} {uuid}",
                                        [("uuid", uuid)], embed=TASK_EMBED))

    def _task_link(self, base: str, links: Sequence[str], task: Ref, link: str) -> Optional[Dict[str, Any]]:
        """Retrieve a linked entity of a task.

        Parameters:
            base: The base URL for the task type
            links: The valid links for the task type
            task: The reference to the task
            link: The linked entity to retrieve

        Returns:
            Optional[Dict[str, Any]]: The linked entity dictionary, or None if not found

        """
        if link not in links:
            raise ValueError(f"link must be one of {tuple(links)}")
        ident = self._id_of(task)
        return self._get_optional(self._url(base, f"/{ident}/{link}"), f"Get {link} of task {ident}")

    # -- pool tasks --------------------------------------------------------

    def get_pool_task(self, task: Ref, embed: EmbedArg = None) -> Dict[str, Any]:
        """Retrieve a pool task by its reference.

        Parameters:
            task: The reference to the pool task
            embed: Optional embedding instructions for the response

        Returns:
            Dict[str, Any]: The pool task dictionary

        """
        ident = self._id_of(task)
        return self._get_json(self._url(POOL_TASKS, f"/{ident}"), f"Get pool task {ident}", embed=embed)

    def get_pool_task_link(self, task: Ref, link: str) -> Optional[Dict[str, Any]]:
        """Retrieve a linked entity of a pool task.

        The link can be one of "workflowitem", "eperson", "group", "step" or "action".

        Parameters:
            task: The reference to the pool task
            link: The linked entity to retrieve

        Returns:
            Optional[Dict[str, Any]]: The linked entity dictionary, or None if not found

        """
        return self._task_link(POOL_TASKS, POOL_TASK_LINKS, task, link)

    def pool_tasks_for_user(self, eperson: Optional[Ref] = None) -> List[Dict[str, Any]]:
        """Tasks the user can claim (default: the current user).

        Parameters:
            eperson: Optional reference to the user (default: the current user)

        Returns:
            List[Dict[str, Any]]: A list of pool task dictionaries

        """
        return self._task_search(POOL_TASKS, "pooltasks", "findByUser", self._user_uuid(eperson))

    def pool_tasks_for_item(self, item: Ref) -> List[Dict[str, Any]]:
        """All pool tasks of an item, for any user (administrators).

        Parameters:
            item: The reference to the item

        Returns:
            List[Dict[str, Any]]: A list of pool task dictionaries

        """
        return self._task_search(POOL_TASKS, "pooltasks", "findAllByItem", self._uuid(item, "item"))

    def find_pool_task(self, item: Ref) -> Optional[Dict[str, Any]]:
        """The pool task of an item the current user may claim, or None.

        Parameters:
            item: The reference to the item

        Returns:
            Optional[Dict[str, Any]]: The pool task dictionary, or None if not found

        """
        uuid = self._uuid(item, "item")
        return self._get_optional(self._url(POOL_TASKS, "/search/findByItem"), f"Find pool task of {uuid}",
                                  params={"uuid": uuid}, embed=TASK_EMBED)

    def claim(self, pool_task: Ref) -> Dict[str, Any]:
        """Claim a pool task.

        The pool task disappears and is replaced by a claimed task.

        Parameters:
            pool_task: The reference to the pool task to claim

        Returns:
            Dict[str, Any]: The claimed task dictionary

        """
        ident = self._id_of(pool_task)
        task = self._request("POST", self._url(CLAIMED_TASKS), f"Claim pool task {ident}", expected=(200, 201),
                             headers=self._headers("text/uri-list"), data=self._url(POOL_TASKS, f"/{ident}")).json()
        logger.info("Claimed pool task %s -> claimed task %s", ident, task.get("id"))
        return task

    # -- claimed tasks -------------------------------------------------------

    def get_claimed_task(self, task: Ref, embed: EmbedArg = None) -> Dict[str, Any]:
        """Retrieve a claimed task by its reference.

        Parameters:
            task: The reference to the claimed task
            embed: Optional embedding instructions for the response

        Returns:
            Dict[str, Any]: The claimed task dictionary

        """
        ident = self._id_of(task)
        return self._get_json(self._url(CLAIMED_TASKS, f"/{ident}"), f"Get claimed task {ident}", embed=embed)

    def get_claimed_task_link(self, task: Ref, link: str) -> Optional[Dict[str, Any]]:
        """Retrieve a linked entity of a claimed task.

        Parameters:
            task: The reference to the claimed task
            link: The linked entity to retrieve ("workflowitem", "owner", "step" or "action")

        Returns:
            Optional[Dict[str, Any]]: The linked entity dictionary, or None if not found

        """
        return self._task_link(CLAIMED_TASKS, CLAIMED_TASK_LINKS, task, link)

    def claimed_tasks_for_user(self, eperson: Optional[Ref] = None) -> List[Dict[str, Any]]:
        """Tasks the user has claimed (default: the current user).

        Parameters:
            eperson: Optional reference to the user (default: the current user)

        Returns:
            List[Dict[str, Any]]: A list of claimed task dictionaries

        """
        return self._task_search(CLAIMED_TASKS, "claimedtasks", "findByUser", self._user_uuid(eperson))

    def claimed_tasks_for_item(self, item: Ref) -> List[Dict[str, Any]]:
        """All claimed tasks of an item, for any user (administrators).

        Parameters:
            item: The reference to the item

        Returns:
            List[Dict[str, Any]]: A list of claimed task dictionaries

        """
        return self._task_search(CLAIMED_TASKS, "claimedtasks", "findAllByItem", self._uuid(item, "item"))

    def find_claimed_task(self, item: Ref) -> Optional[Dict[str, Any]]:
        """The current user's claimed task of an item, or None.

        Parameters:
            item: The reference to the item

        Returns:
            Optional[Dict[str, Any]]: The claimed task dictionary, or None if not found

        """
        uuid = self._uuid(item, "item")
        return self._get_optional(self._url(CLAIMED_TASKS, "/search/findByItem"), f"Find claimed task of {uuid}",
                                  params={"uuid": uuid}, embed=TASK_EMBED)

    def task_options(self, task: Ref) -> List[str]:
        """Options of the task's current action, e.g. ["approve", "reject", "edit_metadata"].

        Parameters:
            task: The reference to the task

        Returns:
            List[str]: A list of available options for the task's current action

        """
        if isinstance(task, dict) and task.get("action"):
            return list(self.get_workflow_action(task["action"]).get("options") or [])
        getter = self.get_claimed_task_link if (isinstance(task, dict) and task.get("type") == "claimedtask") \
            or not isinstance(task, dict) else self.get_pool_task_link
        action = getter(task, "action") or {}
        return list(action.get("options") or [])

    def perform(self, claimed_task: Ref, option: str, **params: Any) -> bool:
        """
        Perform an option of a claimed task: perform(task, "approve"),
        perform(task, "reject", reason="..."). Extra keyword arguments become
        form parameters; list values are repeated (eperson=[a, b]).

        Parameters:
            claimed_task: The reference to the claimed task
            option: The option to perform
            **params: Extra form parameters for the action

        Returns:
            bool: True if the action was successfully performed
        """
        ident = self._id_of(claimed_task)
        form: List[Tuple[str, Any]] = [(submit_param(option), "true")]
        for key, value in params.items():
            if value is None:
                continue
            for v in (value if isinstance(value, (list, tuple)) else [value]):
                form.append((key, v))
        try:
            self._request("POST", self._url(CLAIMED_TASKS, f"/{ident}"), f"Perform {option} on task {ident}",
                          expected=(200, 201, 204), headers=self._headers("application/x-www-form-urlencoded"),
                          data=form)
        except DSpaceError as exc:
            if getattr(exc, "status_code", None) in (403, 404, 422):
                raise WorkflowError(f"Could not {option} claimed task {ident}: HTTP {exc.status_code} "
                                    f"{(exc.response_text or '')[:300]}", status_code=exc.status_code,
                                    response_text=exc.response_text) from exc
            raise
        logger.info("Performed %s on claimed task %s", option, ident)
        return True

    def approve(self, claimed_task: Ref) -> bool:
        """
        Approve a claimed task.

        Parameters:
            claimed_task: The reference to the claimed task

        Returns:
            bool: True if the action was successfully performed
        """
        return self.perform(claimed_task, "approve")

    def reject(self, claimed_task: Ref, reason: str) -> bool:
        """
        Send the submission back to the submitter's workspace with a reason (required).

        Parameters:
            claimed_task: The reference to the claimed task
            reason: The reason for rejection

        Returns:
            bool: True if the action was successfully performed
        """
        if not reason or not reason.strip():
            raise ValueError("A rejection needs a reason")
        return self.perform(claimed_task, "reject", reason=reason)

    def select_reviewers(self, claimed_task: Ref, epersons: Sequence[Ref]) -> bool:
        """
        selectrevieweraction: choose who performs the next step (at least one eperson).
        Sends submit_select_reviewer with eperson=<uuid> values (current DSpace);
        for a single reviewer also the contract's submit_select_reviewer_<uuid>=true
        form, which older versions read.

        Parameters:
            claimed_task: The reference to the claimed task
            epersons: A sequence of references to the reviewers to select

        Returns:
            bool: True if the action was successfully performed
        """
        uuids = [self._uuid(e, "eperson") for e in epersons]
        if not uuids:
            raise ValueError("At least one reviewer is required")
        extra = {f"submit_select_reviewer_{uuids[0]}": "true"} if len(uuids) == 1 else {}
        return self.perform(claimed_task, "select_reviewer", eperson=uuids, **extra)

    def score(self, claimed_task: Ref, score: int, review: Optional[str] = None) -> bool:
        """
        scorereviewaction: record a score (and a review text, required if the
        action is configured with descriptionRequired). The contract names the
        text "description"; DSpace's ScoreReviewAction reads "review" - both are sent.

        Parameters:
            claimed_task: The reference to the claimed task
            score: The score to record
            review: Optional review text

        Returns:
            bool: True if the action was successfully performed
        """
        return self.perform(claimed_task, "score", score=int(score), review=review, description=review)

    def unclaim(self, claimed_task: Ref) -> bool:
        """Give a claimed task back to the pool.

        Parameters:
            claimed_task: The reference to the claimed task

        Returns:
            bool: True if the action was successfully performed
        """
        ident = self._id_of(claimed_task)
        self._request("DELETE", self._url(CLAIMED_TASKS, f"/{ident}"), f"Unclaim task {ident}", expected=(204,))
        logger.info("Claimed task %s returned to the pool", ident)
        return True

    # ==================================================================
    # Higher-level flows
    # ==================================================================

    def claim_for_item(self, item: Ref) -> Dict[str, Any]:
        """The current user's claimed task of the item, claiming the pool task if needed.
        
        Parameters:
            item: The reference to the item

        Returns:
            Dict[str, Any]: The claimed task for the item
        """
        claimed = self.find_claimed_task(item)
        if claimed:
            return claimed
        pool_task = self.find_pool_task(item)
        if not pool_task:
            raise WorkflowError(f"No task for item {self._uuid(item, 'item')} that the current user can claim "
                                "(not in workflow, or not a member of the step's group)")
        return self.claim(pool_task)

    def review(self, item: Ref, decision: str, reason: Optional[str] = None, **params: Any) -> Dict[str, Any]:
        """
        Claim (if needed) and decide on the item's current step:
        decision "approve", "reject" (reason required) or any option of the
        step's action. Returns {"task", "decision", "workflow_item"} -
        workflow_item is the item's workflow item afterwards (None once archived
        or returned to the submitter).

        Parameters:
            item: The reference to the item
            decision: The decision to make ("approve", "reject", or any step action)
            reason: Optional reason for the decision (required for "reject")
            **params: Extra form parameters for the action

        Returns:
            Dict[str, Any]: The result of the review, including "task", "decision", and "workflow_item"
        """
        task = self.claim_for_item(item)
        if decision == "reject":
            self.reject(task, reason or "")
        else:
            self.perform(task, decision, reason=reason, **params)
        return {"task": task, "decision": decision, "workflow_item": self.find_by_item(item)}

    def approve_all_steps(self, item: Ref, max_steps: int = 10) -> int:
        """
        Approve step after step until the item leaves the workflow (archived).
        Returns the number of approvals. Stops with WorkflowError if a step
        cannot be claimed by the current user.

        Parameters:
            item: The reference to the item
            max_steps: The maximum number of steps to approve before stopping

        Returns:
            int: The number of approvals performed
        """
        approvals = 0
        while self.find_by_item(item) is not None:
            if approvals >= max_steps:
                raise WorkflowError(f"Item still in workflow after {max_steps} approvals")
            self.approve(self.claim_for_item(item))
            approvals += 1
        return approvals

    def pending_tasks(self, eperson: Optional[Ref] = None, claimed: bool = True, pool: bool = True
                      ) -> List[Dict[str, Any]]:
        """
        Pending tasks for the user, either claimed or in the pool.

        Parameters:
            eperson: The reference to the user (default: current user)
            claimed: Include claimed tasks (default: True)
            pool: Include pool tasks (default: True)

        Returns:
            List[Dict[str, Any]]: The list of pending tasks for the user

        What waits for a user (default: current user), as a flat list:
        [{"kind": "claimed"|"pool", "task_id", "step", "action", "options",
          "workflow_item_id", "item_uuid", "title"}].
        """
        user = self._user_uuid(eperson)
        tasks = [("claimed", t) for t in (self.claimed_tasks_for_user(user) if claimed else [])]
        tasks += [("pool", t) for t in (self.pool_tasks_for_user(user) if pool else [])]
        result = []
        for kind, task in tasks:
            wfi = (task.get("_embedded") or {}).get("workflowitem") or {}
            item = (wfi.get("_embedded") or {}).get("item") or {}
            titles = (item.get("metadata") or {}).get("dc.title") or []
            result.append({
                "kind": kind, "task_id": task.get("id"), "step": task.get("step"), "action": task.get("action"),
                "options": self.task_options(task) if task.get("action") else [],
                "workflow_item_id": wfi.get("id"), "item_uuid": item.get("uuid"),
                "title": titles[0].get("value") if titles else item.get("name"),
            })
        return result

    # ==================================================================
    # Workflow configuration
    # ==================================================================

    def get_workflow_definitions(self) -> List[Dict[str, Any]]:
        """List all workflow definitions.

        Returns:
            List[Dict[str, Any]]: The list of workflow definitions
        """
        return list(self._iter_embedded(self._url(DEFINITIONS), "workflowdefinitions", "List workflow definitions"))

    def get_workflow_definition(self, name: str) -> Dict[str, Any]:
        """Get a specific workflow definition by name.

        Parameters:
            name: The name of the workflow definition

        Returns:
            Dict[str, Any]: The workflow definition
        """
        return self._get_json(self._url(DEFINITIONS, f"/{name}"), f"Get workflow definition {name}")

    def definition_for_collection(self, collection: Ref) -> Dict[str, Any]:
        """The workflow definition a collection uses (falls back to the default one).

        Parameters:
            collection: The reference to the collection

        Returns:
            Dict[str, Any]: The workflow definition for the collection
        """
        uuid = self._uuid(collection, "collection")
        return self._get_json(self._url(DEFINITIONS, "/search/findByCollection"),
                              f"Workflow definition of {uuid}", params={"uuid": uuid})

    def definition_collections(self, name: str) -> List[Dict[str, Any]]:
        """Collections that explicitly use a workflow definition.

        Parameters:
            name: The name of the workflow definition

        Returns:
            List[Dict[str, Any]]: The list of collections using the workflow definition
        """
        return list(self._iter_embedded(self._url(DEFINITIONS, f"/{name}/collections"), "collections",
                                        f"Collections of workflow definition {name}"))

    def definition_steps(self, name: str) -> List[Dict[str, Any]]:
        """Steps of a workflow definition.

        Parameters:
            name: The name of the workflow definition

        Returns:
            List[Dict[str, Any]]: The list of steps for the workflow definition
        """
        return list(self._iter_embedded(self._url(DEFINITIONS, f"/{name}/steps"), ("steps", "workflowsteps"),
                                        f"Steps of workflow definition {name}"))

    def get_workflow_step(self, name: str) -> Dict[str, Any]:
        """Get a specific workflow step by name.

        Parameters:
            name: The name of the workflow step

        Returns:
            Dict[str, Any]: The workflow step details, including embedded workflow actions
        """
        return self._get_json(self._url(STEPS, f"/{name}"), f"Get workflow step {name}")

    def get_workflow_action(self, name: str) -> Dict[str, Any]:
        """Get a specific workflow action by name.

        Parameters:
            name: The name of the workflow action

        Returns:
            Dict[str, Any]: The workflow action details (cached)
        """
        if name not in self._action_cache:
            self._action_cache[name] = self._get_json(self._url(ACTIONS, f"/{name}"), f"Get workflow action {name}")
        return self._action_cache[name]

    # ==================================================================
    # Supervision orders
    # ==================================================================

    def iter_supervision_orders(self, embed: EmbedArg = ("item", "group")) -> Iterator[Dict[str, Any]]:
        """All supervision orders (administrators). A generator.

        Parameters:
            embed: The related entities to embed (default: ("item", "group"))

        Returns:
            Iterator[Dict[str, Any]]: The supervision orders
        """
        return self._iter_embedded(self._url(SUPERVISION_ORDERS), "supervisionorders", "List supervision orders",
                                   embed=list(embed) if embed else None)

    def get_supervision_order(self, order_id, embed: EmbedArg = None) -> Dict[str, Any]:
        """Get a specific supervision order by ID.

        Parameters:
            order_id: The ID of the supervision order
            embed: The related entities to embed (default: None)

        Returns:
            Dict[str, Any]: The supervision order details
        """
        return self._get_json(self._url(SUPERVISION_ORDERS, f"/{order_id}"), f"Get supervision order {order_id}",
                              embed=embed)

    def supervision_orders_for_item(self, item: Ref) -> List[Dict[str, Any]]:
        """Get all supervision orders for a specific item.

        Parameters:
            item: The reference to the item

        Returns:
            List[Dict[str, Any]]: The supervision orders for the item
        """
        uuid = self._uuid(item, "item")
        return list(self._iter_embedded(self._url(SUPERVISION_ORDERS, "/search/byItem"), "supervisionorders",
                                        f"Supervision orders of {uuid}", [("uuid", uuid)], embed=["group"]))

    def create_supervision_order(self, item: Ref, group: Ref, order_type: str = "NONE") -> Dict[str, Any]:
        """
        Let a group supervise an in-progress submission (workspace or workflow).
        order_type: NONE (no extra rights), EDITOR (READ + WRITE on the item and
        its files) or OBSERVER (READ). 422: item not in progress, or the group
        already has an order for it.

        Parameters:
            item: The reference to the item
            group: The reference to the group
            order_type: The type of supervision order (default: "NONE")

        Returns:
            Dict[str, Any]: The created supervision order
        """
        order_type = order_type.upper()
        if order_type not in SUPERVISION_TYPES:
            raise ValueError(f"order_type must be one of {SUPERVISION_TYPES}")
        params = {"uuid": self._uuid(item, "item"), "group": self._uuid(group, "group"), "type": order_type}
        order = self._request("POST", self._url(SUPERVISION_ORDERS), "Create supervision order", expected=(200, 201),
                              params=params, headers=self._headers()).json()
        logger.info("Created %s supervision order %s on item %s", order_type, order.get("id"), params["uuid"])
        return order

    def delete_supervision_order(self, order_id) -> bool:
        """Delete a specific supervision order by ID.

        Parameters:
            order_id: The ID of the supervision order

        Returns:
            bool: True if the deletion was successful
        """
        self._request("DELETE", self._url(SUPERVISION_ORDERS, f"/{order_id}"), f"Delete supervision order {order_id}",
                      expected=(204,))
        return True
