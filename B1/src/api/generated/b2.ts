/**
 * AUTO-GENERATED FROM openapi/b2.yaml — DO NOT EDIT BY HAND.
 *
 * Regenerate with: pnpm api:generate
 * Source of truth: MAIN/contracts/** (see docs/adr/ADR-0002-contract-generated-api-client.md).
 * Any hand edit is reported as contract drift by `pnpm api:check`.
 */

export interface paths {
    "/api/v1/submissions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Accept a submission for evaluation (RQ-B-2-10)
         * @description Validates the submission envelope against MAIN/contracts/schemas/
         *     submission-envelope.schema.json, re-computes each file digest, enforces file
         *     count and size limits, and stores content by digest. Repeating a request with the
         *     same idempotency key returns the same submission_id and does not start a second
         *     evaluation.
         *
         *     One request is one assignment question (v0.32.0). The envelope carries
         *     `assignment_id`; the server asks B3 for the assignment (course, open state, ordered
         *     question list) and asks **B4** for the submitting student's current effective course
         *     membership. The section recorded on the submission is the one B4 reports, not the one
         *     the client claimed: a token's scope is only a snapshot, so admission must not depend
         *     on it. The request is refused when the question does not belong to the assignment
         *     (`422`) or when the student is not currently enrolled in the assignment's course
         *     (`403`). Exactly one `.txt` answer file is accepted, named after the question's
         *     1-based position in the assignment (`1.txt`, `2.txt`, ...).
         *
         *     On success emits `autograder.v1.submission.accepted` (including `assignment_id`);
         *     the event never carries source code.
         */
        post: operations["createSubmission"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/submissions/{submission_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submission_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Read submission metadata and state
         * @description Returns state machine position and file metadata (path plus digest). Source
         *     content is not inlined here; it is served through a separate authorized
         *     endpoint so that authorization can be enforced per read.
         */
        get: operations["getSubmission"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/evaluation-runs/{evaluation_run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /** Read evaluation run state, stage and progress */
        get: operations["getEvaluationRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/evaluation-runs/{evaluation_run_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Stream evaluation progress as Server-Sent Events (RQ-B-1-50)
         * @description Session-scoped progress stream. Events carry a monotonically increasing
         *     `sequence` so a reconnecting client can deduplicate and order without
         *     replaying history. Progress events never carry hidden test inputs, expected
         *     outputs or source code: they may be shown to the student.
         */
        get: operations["streamEvaluationEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/evaluation-runs/{evaluation_run_id}/report": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Read the explainable evaluation report (RQ-B-2-80)
         * @description Returns overall assessment plus per-item detail. Every awarded or deducted
         *     point traces to a rule or a test case, and the sum of item scores must equal
         *     the total; a mismatch is a defect, not a rounding artefact. The student view
         *     never contains reference answers, hidden cases or another student's code.
         */
        get: operations["getEvaluationReport"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/evaluation-runs/{evaluation_run_id}/commentary": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Read the teaching commentary for a run (RQ-T-20)
         * @description The **non-deterministic** track. `ADR-0009` replaced ADR-0005's blanket ban on model
         *     output with a two-track design: the score track stays fully deterministic, and this
         *     separate track may carry model-generated teaching commentary.
         *
         *     Four properties make that separation real rather than nominal, and all four are
         *     normative here rather than advisory:
         *
         *     1. **The input is a whitelist.** Commentary may be generated only from structured
         *        evidence already stored for the run -- per-case outcomes and failure categories,
         *        static rule hits, the three-state security conclusion, the score breakdown, and the
         *        question's *public* metadata. Reading student source, reference answers, hidden
         *        cases or expected outputs to produce it is forbidden. The whitelist is what keeps
         *        the model from ever seeing protected content, so it is a security boundary, not a
         *        quality preference.
         *     2. **It never enters the score track.** No commentary value appears in the score,
         *        `evaluation_results`, `case_evidence`, `result_digest`, the five-part binding
         *        summary or the grade export. Deleting this table must leave reproducibility intact.
         *     3. **Provenance is returned, not implied.** Every response carries the provider, model
         *        and prompt-template version that produced it.
         *     4. **Unavailable is a normal answer.** The model service is **off by default**
         *        (`ADR-0009` decision 4); with no provider configured this returns `status:
         *        "unavailable"` rather than an error, because "this deployment does not generate
         *        commentary" is a configuration, not a failure.
         */
        get: operations["getEvaluationCommentary"];
        put?: never;
        /**
         * Generate (or regenerate) the teaching commentary for a run (RQ-T-20)
         * @description Teacher only: generating commentary is an editorial act, and `ADR-0009`
         *     decision 5 keeps student visibility a course-level policy that defaults to off -- so
         *     the request is not the student's to make.
         *
         *     Regeneration is always allowed and never changes a score. When no provider is
         *     configured the call returns `409` with a detail naming the missing configuration, so
         *     the operator learns what to set instead of seeing a generic failure.
         */
        post: operations["requestEvaluationCommentary"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/evaluation-runs/{evaluation_run_id}/learning-hints": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Read the caller's layered learning hints for one evaluation run (AI-FLOW-01B)
         * @description **Owner-only.** These hints belong to the student who owns the submission behind the run;
         *     teacher and assistant roles are refused (403) — there is deliberately no staff endpoint to
         *     read an individual student's hints, because hints are a learning aid, not evidence.
         *
         *     `status` is one of `not_requested` (provider is configured but nothing was generated for
         *     this run yet), `ready` (1-3 ordered steps), `failed` (generation was attempted and did not
         *     produce a safe result), `unavailable` (this deployment configures no model provider and no
         *     earlier result exists) or `not_applicable` (the run is completed but has no deterministic
         *     failure evidence to explain). A previously generated result stays readable after the
         *     provider is switched off: configuration is not a reason to retract content.
         *
         *     The response never contains the score, case evidence, `result_digest`, hidden cases,
         *     reference answers or another student's data. Hints are **not** part of the score track.
         */
        get: operations["getLearningHints"];
        put?: never;
        /**
         * Generate (or idempotently replay) layered learning hints for one run (AI-FLOW-01B)
         * @description **Owner-only and completed-run-only.** The request carries no model parameters, no question
         *     content and no student id: the provider/model come from deployment configuration, the
         *     public question context is read server-side from B3 by `run.question_version_id`, and
         *     ownership is derived from the submission, never from the body.
         *
         *     Preconditions, each with a distinct answer:
         *
         *     - run not `completed` -> `409` ("评测完成后可生成提示"), and no provider call happens;
         *     - run rejected by security rules or carrying a `found` security conclusion -> `409`;
         *       hints must not suggest a workaround;
         *     - terminal reason is an infrastructure/internal error -> `409`; a system failure must never
         *       be attributed to the student;
         *     - no failed case and no blocking static finding -> `200` with `status: not_applicable`
         *       (no invented failure reason, no model call);
         *     - deployment budget for this student exhausted -> `429`;
         *     - provider not configured -> `503` (the caller can still GET the authoritative score and
         *       report);
         *     - provider transport failure -> `503` with a Problem Details body; the stored row is
         *       `failed` and remains readable via GET;
         *     - output that is structurally invalid or trips the safe-output guard -> `200` with
         *       `status: failed` (no partial steps are ever returned).
         *
         *     Replaying the same `Idempotency-Key` returns the stored result without another model call.
         *     A run has at most one hint row: a successful result is final for that run, while a `failed`
         *     row may be retried explicitly (a new submission always creates a new run and its own hints).
         */
        post: operations["requestLearningHints"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/intake/quarantines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List mail that could not be matched to a submission (CHG-0014)
         * @description `b2.intake_quarantines` has been written since the email-intake path was built -- a
         *     message whose assignment identifier cannot be resolved is recorded there rather than
         *     silently dropped -- but there was **no way to read it**. The integration audit put it
         *     plainly: unmatched mail was, from a teacher's point of view, disappearing without a
         *     trace. This is the read path.
         *
         *     Authorization: teacher/admin within the row's course scope. Rows carry a
         *     `reason_code`, an already-redacted `detail`, the record's own
         *     `scope_course_id/scope_section_id`, and a bounded `subject` header (printable characters
         *     only, at most 200 characters; bodies and attachments are still never stored); they never
         *     carry attachment contents, student source or any part of a submission.
         *
         *     **Scoping is per row** (P13). A row's scope is persisted when the mail path records it,
         *     and it comes from exactly one place: the matching rules' targets. When every matched rule
         *     target names the same `(course_id, section_id)` that pair is stored; `not_matched`,
         *     candidates spanning courses/sections, and envelope/attachment rejections store `null`.
         *     The record's scope is never derived from the mailbox, the sender, the attachment names or
         *     this request -- those are attacker-controlled inputs, and deriving scope from them would
         *     forge the authorization basis.
         *
         *     What that means for callers:
         *
         *     * staff receive only rows whose `scope_course_id/scope_section_id` are covered by one of
         *       their token scopes; rows with a null scope are invisible to them;
         *     * administrators receive the global list, including unscoped rows (they are the only role
         *       that may process those);
         *     * `course_id/section_id` only narrow the caller's visible set. They are a filter, not a
         *       grant: a staff caller naming a course/section outside the token scope gets `403`, not an
         *       empty page. `section_id` requires `course_id`.
         *
         *     The row predicate is applied in SQL **before** ordering, `limit` and the cursor, so the
         *     page contents, `next_cursor` and any counts derived from the page never hint at hidden
         *     rows. A cursor is only a position, never a filter or an identity, so reusing one cannot
         *     widen what the caller may read.
         */
        get: operations["listIntakeQuarantines"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/intake/quarantines/{intake_quarantine_id}/resolve": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Attribute a quarantined message to a student (CHG-0014)
         * @description A teacher who recognises the message attributes it manually. Three properties are
         *     deliberately not negotiable:
         *
         *     1. **Only identity failures are attributable** (`CHG-0021` §2.1). Attribution supplies the
         *        *identity* that automatic matching could not derive. It re-runs the checks that are
         *        still runnable from the data on hand -- role and course scope -- plus one content gate:
         *
         *        > Only a record whose `reason_code` is `not_uniquely_matched` or `ambiguous_match` can be
         *        > attributed. **Every other reason code is refused with `422`.**
         *
         *        The earlier wording was "the manual path re-runs the same validation as the automatic
         *        one", which is not decidable here: `b2.intake_quarantines` stores mailbox, uidvalidity,
         *        uid, message-id, `reason_code` and `detail` -- **no message bytes, no attachment bytes, no
         *        digest of either** (a direct consequence of the platform not becoming a second mail
         *        store). There is nothing left to recompute. Genuinely re-running the envelope checks
         *        would mean re-fetching the message over IMAP, and this operation declares no `503` -- so
         *        the contract never intended it to depend on the mailbox. Naming a student cannot repair
         *        "the attachment exceeded the size limit": allowing that would turn attribution into a way
         *        around the automatic envelope checks.
         *     2. **Resolving does not silently re-evaluate** (`CHG-0021` §2.3). It records the attribution
         *        and leaves evaluation to the normal submission path, so the audit trail shows a human
         *        decision followed by the ordinary machine flow. It **does not build a submission**: doing
         *        so would also need `section_id` and `question_version_id`, which the matching rules supply
         *        and which do not exist when matching failed. Closing that loop is a separate design, not
         *        a patch to this operation.
         *     3. **Attribution is recorded once.** A second attempt returns `409`; the row is never
         *        silently re-attributed. The same holds for a row already dismissed by
         *        `dismissIntakeQuarantine` -- the two decisions share one "processed" state.
         *
         *     Each decision also writes `resolution=attributed`, `resolved_at` (server clock) and
         *     `resolved_by` (the caller's platform user id). Those fields are what the review screen
         *     shows as the processing trail; `null` on historical rows means "not recorded", never
         *     "processed by nobody".
         *
         *     **Row scope is re-checked under the row lock (P13).** The record's
         *     `scope_course_id/scope_section_id` is the authoritative course association, and the
         *     authorization decision is made against the **locked** row before any write:
         *
         *     * staff: a null scope or a scope outside the token's course scope is refused with `403`.
         *       Unscoped records are not theirs to process -- otherwise "the matcher could not derive an
         *       identity" would become "any staff member may claim it";
         *     * administrator: may attribute unscoped records. The UI's explicit course/section choice
         *       is only how it loads candidate students; it is **not** part of this body and does not
         *       rewrite the record's original scope, which stays null.
         *
         *     `student_id` is the platform user id -- the same key `CHG-0007` added to the roster, so
         *     both sides of "who is this" finally use one identifier.
         *
         *     Stated plainly here rather than left to be assumed: roster membership ("is this student
         *     still enrolled in the record's section?") is B4's fact. B2 has no B2->B4 roster channel,
         *     so this operation does **not** re-verify membership; the candidate list is always fetched
         *     fresh from B4 by the client, and the server re-verifies the record's row scope and the
         *     caller's role/scope instead of trusting the client's candidate list.
         */
        post: operations["resolveIntakeQuarantine"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/intake/quarantines/{intake_quarantine_id}/dismiss": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Dismiss a quarantined message without attributing it
         * @description A teacher who has looked at a quarantined message can close it without naming a student.
         *     This is deliberately a separate operation from attribution: attribution writes an
         *     identity and is therefore limited to the two "identity could not be derived" reason
         *     codes, while dismissal writes **no** identity and is valid for **every** reason code --
         *     it only says "a human has reviewed this row and chose not to attribute it". Without it,
         *     rows rejected by the automatic envelope checks could never leave the pending list.
         *
         *     The write is the same shape as attribution: one `UPDATE` on the locked row, no
         *     submission, no outbox event, no evaluation. `resolution` becomes `dismissed`,
         *     `resolved_student_id` stays `null`, and `resolved_at`/`resolved_by` record who closed
         *     it and when.
         *
         *     Row-level authorization is identical to `resolveIntakeQuarantine`: staff only for rows
         *     covered by their token course scope (null-scope rows are invisible), administrators for
         *     any row. The row is locked and re-authorized before the already-resolved check, so a
         *     caller outside the row's scope cannot learn its state from the status code.
         *
         *     A row that has already been attributed **or** dismissed returns `409`; neither decision
         *     is silently overwritten.
         */
        post: operations["dismissIntakeQuarantine"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/intake/quarantines/{intake_quarantine_id}/hold": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Put a quarantined message on hold, or resume it ("暂存")
         * @description A reviewer can defer a row without deciding it. A held row is **not** resolved: it keeps
         *     its place in the review flow, can still be completed or dismissed, and can be resumed with
         *     `held=false`. The hold itself records who and when (`held_at`/`held_by`).
         *
         *     Row authorization is identical to the other intake operations: staff only for rows covered
         *     by their token scope, administrators for any row; a resolved row returns `409`.
         */
        post: operations["setIntakeQuarantineHold"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/intake/quarantines/{intake_quarantine_id}/complete": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Match the quarantined message to a student and create that student's submissions
         * @description "Match user" closes the loop that `resolveIntakeQuarantine` deliberately left open: the
         *     server re-reads the **original message** from the mailbox position stored on the row
         *     (`mailbox`/`uidvalidity`/`uid`), re-runs the attachment gates, maps each attachment to
         *     its question by the `<n>.txt` name, and persists a normal submission per mapped question
         *     for the explicitly chosen `student_id`/`course_id`/`section_id`/`assignment_id`. The
         *     quarantine row becomes `resolution=completed` and carries the first created submission
         *     as `resolved_submission_id`; `email_checkpoints` moves from `quarantined` to `processed`
         *     in the same submission transaction.
         *
         *     Preconditions and refusals:
         *
         *     * the row must be visible and processable under the same row-level scope rules as the
         *       other intake operations; for a scoped row the chosen course/section must equal the
         *       record's proven scope (`422` otherwise), and staff must hold the target scope (`403`);
         *     * the chosen student must be in the assignment's audience and the assignment must belong
         *       to the chosen course (`403`/`422`);
         *     * a resolved row returns `409`; a row whose message is gone (UIDVALIDITY changed, deleted,
         *       fetch failed) returns `409` -- the platform does not store attachment bytes, so it cannot
         *       invent them;
         *     * the original message must still pass the current attachment gates (`422` otherwise); a
         *       message whose attachments cannot be mapped to the assignment (`<n>.txt`, n within the
         *       question list) is `422`;
         *     * if email intake is not configured in this deployment the operation returns `503`:
         *       there is no cached copy to fall back to.
         *
         *     The operation writes normal submissions (same persist path as the automatic flow), so
         *     evaluation/outbox behaviour is exactly that of a submission created at intake time.
         */
        post: operations["completeIntakeQuarantine"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/reference-verifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Run a version's own reference answer and report per-case outcomes (CHG-0019)
         * @description `publish` requires a reference-verification attestation bound to the version's
         *     `content_digest`, and that gate is real: B3 cross-checks the digest and there is no skip
         *     switch. Its design premise is **executor is not recorder** -- B3 executes nothing, so
         *     something that *can* execute has to run the reference answer.
         *
         *     That premise left the authoring UI stuck: a teacher cannot honestly self-attest, and a
         *     browser must not hold `serviceToken`. This endpoint is the resolution (ADR-0015): **B2
         *     executes, B3 records, the teacher triggers it with an ordinary user token.** B2 uses the
         *     B3 service channel it already has for fetching evaluation packages, so no new credential,
         *     no new trust relationship and no new cross-module mechanism is introduced.
         *
         *     Two properties are deliberate:
         *
         *     * **Nothing is persisted here.** The call reports what it observed. The attestation is
         *       written by B3, and only when the reference answer actually passed -- a failed
         *       verification must not leave a record that could later be mistaken for one.
         *     * **No protected content comes back.** The response is counts plus a per-case outcome and
         *       failure category. Expected output, hidden-case input and the reference answer's source
         *       are all absent: a teacher may see those through the authoring surface, but this endpoint
         *       must not become a second route to them (same granularity as the report's `view=teacher`,
         *       not more).
         *
         *     On a platform without the sandbox this returns `503` and records nothing. Reporting
         *     success it did not achieve would defeat the entire gate, so it does not pretend.
         */
        post: operations["verifyReferenceAnswer"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/evaluation-runs/{evaluation_run_id}/appeal-pack": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Export one run's evidence as a self-contained, movable bundle (CHG-0014)
         * @description A grade dispute is answered with evidence, and the evidence has to be *movable*: a
         *     reviewer who is not logged into this deployment must be able to check the decision from
         *     the file alone. The pack therefore embeds the report, the five binding digests, the
         *     runner-policy summary and an explicit replay instruction, and is pinned by
         *     `result_digest` so a reader can tell whether the pack was altered.
         *
         *     Two redactions are mandatory, and they are why this is a purpose-built document rather
         *     than "the report endpoint with a download header":
         *
         *     * **host filesystem paths are removed** -- a pack leaves the machine, and absolute paths
         *       describe internal layout;
         *     * **the student view is never widened by exporting.** A pack carries what the caller may
         *       already see; producing one must not become a way to obtain hidden cases or expected
         *       outputs.
         *
         *     Authorization: teacher/admin, plus the student for their own run.
         */
        get: operations["getAppealPack"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/evaluation-runs/quality-summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Per-case and per-rule quality aggregate for one question version (CHG-0015)
         * @description Answers "is this question actually discriminating?" from data only B2 holds. B3's
         *     statistics are built from de-identified `evaluation.completed` events and therefore see
         *     score buckets only -- the audit's phrasing was that B3 "cannot see per-case or per-rule
         *     detail". This endpoint is where that detail lives, and it stays inside B2 because the
         *     data is B2's.
         *
         *     De-identification is a requirement, not a nicety: the response contains **counts only**
         *     -- no student id, no submission id, no source code, no expected output. It is an
         *     aggregate a teacher reads to decide whether a question is worth keeping, not a way to
         *     look up an individual.
         *
         *     Two fields deserve their own explanation, because both are easy to render dishonestly:
         *
         *     * `security_not_checked` is reported separately from `security_not_found`. A question
         *       whose runs were never security-checked must not look like one that passed.
         *     * `unmeasured_runs` counts runs where the sandbox could not report resource peaks.
         *       Without it, "no resource data" would silently read as "used nothing".
         *
         *     Implementation note (a real constraint, not trivia): this path is a literal segment
         *     under a collection that also has `{evaluation_run_id}` children, so the router must
         *     declare it **before** the parameterised route, or the path parameter will swallow it.
         */
        get: operations["getEvaluationQualitySummary"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/submissions/{submission_id}/manual-grade": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Read the current manual grade (204 when the submission is not graded yet)
         * @description The drawer needs two things this read provides: whether a manual score already exists
         *     (a changed score requires a `reason`) and its `updated_at`, which is the value the next
         *     write must echo as `expected_updated_at`. Letting the client invent that value would hand
         *     the optimistic lock back to the caller.
         *
         *     `204` means "no manual score yet", not "error": a submission waiting for its first grade
         *     is the normal state of a mailed-in assignment.
         */
        get: operations["getManualGrade"];
        put?: never;
        /**
         * Record a teacher's score for a submission (manual grading)
         * @description Manual grading exists because some assignments cannot be judged by execution — the
         *     Unix homework asks for command lines mailed in, and their output depends on the machine
         *     they run on. The score is the teacher's; B2 stores it with its author, its time and its
         *     history, and reports `score_source: manual` so no export can present it as a machine
         *     result.
         *
         *     Authorization is the course scope of the section the submission belongs to: a teacher of
         *     another course gets 403, and a student cannot grade anything (the token's roles decide,
         *     not the request body).
         */
        post: operations["setManualGrade"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/submissions/{submission_id}/manual-grade/release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Withdraw a manual score and return the submission to "awaiting manual"
         * @description Needed for the mis-click case: a score entered against the wrong submission has to be
         *     removable without editing the database. The prior score stays in the history table.
         */
        post: operations["releaseManualGrade"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/submissions/{submission_id}/files": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Download one file of a submission (student owner or authorized teacher)
         * @description Students must be able to see how their work is judged, and teachers must be able to read
         *     what was actually submitted — including answers that arrived by mail. Read authority is
         *     three-layered (CHG-0032): a recognized role, a token whose course/section scope covers
         *     the submission, and ownership/staff authority — a student may read only their own
         *     submission, a teacher/admin may read submissions in their course scope. Content is
         *     served from the content-addressed store; the response never includes other files, and
         *     every download is audited as `submission.file_downloaded`. Source code and message
         *     bodies are never written to logs.
         */
        get: operations["downloadSubmissionFile"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/submissions/{submission_id}/intake-message": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Read the message a submission arrived in (subject, sender, body excerpt)
         * @description Returns the bounded excerpt described on `IntakeMessageView`. A submission created
         *     through the API has no message, and that is reported as 404 rather than an empty object,
         *     so the caller can tell "mailed in" from "uploaded".
         */
        get: operations["getSubmissionIntakeMessage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/grades": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Query grades by course and section (RQ-B-1-30)
         * @description Teachers see their authorized scope; students see only their own records. An
         *     attempt to widen `student_id` is rejected with 403 rather than silently
         *     narrowed. Runs that have not finished are reported with their state instead
         *     of a zero score, so an unfinished evaluation is never mistaken for a failing
         *     grade.
         */
        get: operations["listGrades"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/grades/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Export grades as XLSX or CSV (RQ-B-1-60, RQ-B-2-60)
         * @description Applies exactly the same filter semantics as `listGrades`, so an export can
         *     never disagree with what the teacher saw. Cell values beginning with =, +, -,
         *     @, tab or carriage return are escaped to prevent formula injection. The export
         *     never contains source code, reference answers or hidden test cases.
         */
        get: operations["exportGrades"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/email-verification/challenges": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Issue a one-time e-mail verification code for an assignment (student)
         * @description Starts the ownership proof for submitting one assignment by mail (CHG-0032). The
         *     student is authenticated; the code is returned **once**, bound to `(student, course,
         *     section, assignment)`, stored only as a SHA-256 digest, and expires after 30 minutes.
         *
         *     Why a code: a `From` header is forgeable, so matching on the sender address alone would
         *     let anyone submit mail as another account. The code travels only through this
         *     authenticated response and must appear in the message; the receiving intake then
         *     requires the sending account to be the challenge owner. That is what makes the link
         *     between a real message and a verified account email a fact rather than a guess.
         *
         *     The message may carry **one `.txt` answer file per question**; the file name is the
         *     question's 1-based position in the assignment (`1.txt`, `2.txt`, ...). No fixed
         *     deploy-time target or sender rule table is involved.
         *
         *     A new code may be requested at any time; each code is single-use, so an old one is not a
         *     replay vector. Issuing a code does not create a submission and does not change any grade.
         */
        post: operations["createEmailVerificationChallenge"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/email-verification/challenges/{challenge_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                challenge_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Read the state of a student's e-mail verification code
         * @description The student-visible feedback channel for the mail path: it distinguishes "message not
         *     received yet", "message received and mapped to per-question submissions", "message
         *     received but rejected (with a stable reason code)" and "code expired". The same
         *     distinction keeps a mail-intake outage from reading as a student answer mistake:
         *     `reason_code = intake_unavailable` belongs to the platform, not the answer. Only the
         *     challenge owner can read it; the response never contains the code, the sender address,
         *     the subject or any message body.
         */
        get: operations["getEmailVerificationChallengeStatus"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * @description The complete, exhaustive set of allowed submission state transitions. Any
         *     transition not listed here is a defect.
         *
         *     `rejected` is reachable from `received`, `validated` and `running`, and this
         *     is deliberate: a submission whose envelope is malformed, whose language does
         *     not match the question, or whose code trips a high-risk static rule must be
         *     refused WITHOUT entering the sandbox. Static analysis therefore runs before
         *     `queued`, and "we ran it and it failed" is never an acceptable substitute for
         *     a refusal. `failed` is reachable only from `running`, because a technical
         *     failure presupposes that execution was attempted.
         */
        SubmissionTransitions: {
            /**
             * @example {
             *       "received": [
             *         "validated",
             *         "rejected"
             *       ],
             *       "validated": [
             *         "queued",
             *         "rejected"
             *       ],
             *       "queued": [
             *         "running"
             *       ],
             *       "running": [
             *         "completed",
             *         "rejected",
             *         "failed"
             *       ],
             *       "completed": [],
             *       "rejected": [],
             *       "failed": []
             *     }
             */
            transitions: {
                [key: string]: ("received" | "validated" | "queued" | "running" | "completed" | "rejected" | "failed")[];
            };
        };
        /**
         * @description Data payload of one SSE frame on `/api/v1/evaluation-runs/{id}/events`.
         *
         *     This is the authoritative delivery channel for terminal states to the B1
         *     client. `rejected` deliberately has no counterpart domain event: a refusal
         *     matters to the submitting session, and broadcasting it system-wide would add
         *     a payload with no system-level consumer. `failed` and `completed` do have
         *     domain events, because other modules consume them.
         *
         *     `sequence` is monotonic per run so a reconnecting client can deduplicate and
         *     order without replaying history. Frames may be shown to the student, so they
         *     never carry hidden case inputs, expected outputs or source code.
         */
        EvaluationStreamFrame: {
            evaluation_run_id: components["schemas"]["Uuid"];
            /**
             * @description Terminal frames are completed, rejected and failed; every other update is progress.
             * @enum {string}
             */
            event: "progress" | "completed" | "rejected" | "failed";
            /** @enum {string} */
            state: "received" | "validated" | "queued" | "running" | "completed" | "rejected" | "failed";
            /** @enum {string} */
            stage: "queued" | "fetching_package" | "static_analysis" | "security_check" | "dynamic_execution" | "scoring" | "reporting" | "terminal";
            /** @description Monotonic. Deduplicate and order by this value. */
            sequence: number;
            cases_total?: number | null;
            cases_done?: number | null;
            /** @description Present on a completed frame; null otherwise. Never rendered as 0 for an unfinished run. */
            score?: number | null;
            is_pass?: boolean | null;
            /**
             * @description Why the run ended. 'static_blocked' and 'security_blocked' accompany rejected; 'infrastructure_error' and 'internal_error' accompany failed.
             * @enum {string|null}
             */
            terminal_reason?: null | "static_blocked" | "security_blocked" | "infrastructure_error" | "internal_error";
            /** @description Redacted, human-readable. No stack traces, internal paths or source code. */
            message?: string | null;
            occurred_at?: components["schemas"]["Timestamp"];
        };
        SubmissionAccepted: {
            submission_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            state: "received" | "validated" | "queued" | "running" | "completed" | "rejected" | "failed";
            evaluation_run_id?: components["schemas"]["Uuid"] | null;
            /** @description True when this response replays a previous submission with the same idempotency key. */
            idempotent_replay: boolean;
            accepted_at?: components["schemas"]["Timestamp"];
        };
        SubmissionView: {
            submission_id: components["schemas"]["Uuid"];
            /**
             * @description The assignment this submission answers. Null only for submissions accepted before
             *     assignments were threaded through the intake (CHG-0032): those rows are preserved
             *     as-is, never attributed to an assignment by guesswork, and never appear in an
             *     assignment-scoped query.
             */
            assignment_id: components["schemas"]["Uuid"] | null;
            student_id?: components["schemas"]["Uuid"];
            course_id?: components["schemas"]["Uuid"];
            section_id?: components["schemas"]["Uuid"];
            question_version_id?: components["schemas"]["Uuid"];
            /** @enum {string} */
            state: "received" | "validated" | "queued" | "running" | "completed" | "rejected" | "failed";
            /** @enum {string} */
            intake: "api" | "email";
            /**
             * @description The evaluation run once it exists, else null. Needed because the run row is
             *     created *after* acceptance, so `POST /submissions` legitimately answers `202`
             *     with a null run id while the progress stream is addressed by run id. Without
             *     this field no client can get from a submission to its run, which makes the
             *     real-time progress feature unreachable.
             */
            evaluation_run_id?: components["schemas"]["Uuid"] | null;
            /** @description Paths and digests only; source content is served through a separately authorized read. */
            files: {
                path: string;
                content_sha256: components["schemas"]["Sha256"];
                size_bytes: number;
            }[];
            state_history?: components["schemas"]["StateTransition"][];
            created_at: components["schemas"]["Timestamp"];
        };
        StateTransition: {
            from_state: string | null;
            to_state: string;
            occurred_at: components["schemas"]["Timestamp"];
            /** @description Redacted, human-readable cause. Never contains a stack trace or source code. */
            reason?: string | null;
        };
        /**
         * @description One quarantined intake record (CHG-0014). Carries identification metadata, a bounded
         *     subject header and a redacted reason, never message bodies or attachments: the point is
         *     that a teacher can *find* the message in their mailbox again, not that the platform
         *     becomes a second mail store.
         *
         *     `scope_course_id/scope_section_id` are the record's **verified course association**
         *     (P13). They are persisted at intake time from the matching rules' targets and only when
         *     every matched target agrees on the same pair; `not_matched`, candidates spanning
         *     courses/sections, and envelope/attachment rejections leave them `null` ("unscoped").
         *     The pair is either both null or both non-null, and it is the field the server uses for
         *     row-level authorization: staff never see null-scope rows, and administrators are the
         *     only role that may read/process them.
         */
        IntakeQuarantineView: {
            intake_quarantine_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            intake: "email";
            /**
             * @description Machine-readable cause. **The vocabulary is closed** (`CHG-0021` §2.1, made
             *     machine-readable by `CHG-0027`); the previous description was an open "e.g." set, which
             *     left a consumer unable to tell whether a code it saw was one it should handle.
             *
             *     The `enum` is the same 19 values the table below groups, and it is a fact rather than a
             *     preference: B2's `EmailIntakeReason` is a `Literal` of exactly these, and the write path
             *     produces no others. A consumer can therefore generate the type instead of copying the
             *     list -- which the previous prose-only form forced, in violation of the rule that field
             *     definitions live only in `contracts/**`.
             *
             *     | Group | Codes | Attributable |
             *     |---|---|---|
             *     | Identity not derived | `not_uniquely_matched`, `ambiguous_match` | **yes** |
             *     | Message structure | `malformed_message`, `missing_message_id`, `no_attachments`, `too_many_attachments`, `message_too_large` | no |
             *     | Attachment policy | `attachment_name_not_allowed`, `duplicate_attachment_name`, `unsupported_attachment_encoding`, `malformed_attachment`, `attachment_too_large`, `attachment_total_too_large`, `unsupported_attachment_type`, `nested_message_attachment` | no |
             *     | Content gates | `executable_attachment`, `archive_bomb`, `archive_attachment`, `not_plain_text` | no |
             *
             *     Only the first two are attributable, because only they mean "the identity could not be
             *     derived". `checkpoint_exists` is deliberately **not** in this table: it is an
             *     `email_checkpoints` outcome, not an `intake_quarantines.reason_code`.
             * @enum {string}
             */
            reason_code: "not_uniquely_matched" | "ambiguous_match" | "malformed_message" | "missing_message_id" | "no_attachments" | "too_many_attachments" | "message_too_large" | "attachment_name_not_allowed" | "duplicate_attachment_name" | "unsupported_attachment_encoding" | "malformed_attachment" | "attachment_too_large" | "attachment_total_too_large" | "unsupported_attachment_type" | "nested_message_attachment" | "executable_attachment" | "archive_bomb" | "archive_attachment" | "not_plain_text" | "email_verification_rejected" | "email_sender_mismatch" | "email_verification_expired" | "email_body_missing_field" | "email_body_mismatch" | "attachment_mapping_invalid";
            /**
             * @description Already redacted when recorded; bounded here as a backstop.
             *     **`1000`, not `500`** (`CHG-0021` §2.4): the database CHECK is
             *     `char_length(detail) BETWEEN 1 AND 1000` and the writer truncates at `1000`, so a
             *     `500` bound was a claim the server itself could violate -- it would have rejected its
             *     own valid output.
             */
            detail: string;
            /** @description Which mailbox, so the teacher can locate the original. */
            mailbox: string;
            /** @description RFC 5322 Message-ID. An identifier, not content. */
            message_id?: string | null;
            /** @description IMAP UID for locating the message again. */
            uid?: number | null;
            recorded_at: components["schemas"]["Timestamp"];
            resolved: boolean;
            resolved_student_id?: components["schemas"]["Uuid"] | null;
            resolution_note?: string | null;
            /**
             * @description The record's verified course, or `null` when no course association could be proven.
             *     Persisted from the matching rules' targets at intake time (never from the mailbox,
             *     sender, attachment names or a request parameter). Null means "unscoped": only
             *     administrators may see or process such a row.
             */
            scope_course_id: components["schemas"]["Uuid"] | null;
            /**
             * @description The record's verified section. Either both scope fields are null or both are set;
             *     the database enforces that as a CHECK constraint.
             */
            scope_section_id: components["schemas"]["Uuid"] | null;
            /**
             * @description The message's Subject header, collapsed to printable characters and bounded to 200
             *     characters at intake time. This is the only content-adjacent field the platform
             *     keeps: message bodies, attachments and their digests are still never stored, and the
             *     subject is only shown to a caller already authorized for the row. Rows recorded
             *     before this field existed return `null`.
             */
            subject: string | null;
            /**
             * @description The human decision: `completed` (the original mail was re-read and turned into the
             *     chosen student's submission), `attributed` (legacy manual attribution that recorded a
             *     student but did not create a submission), `dismissed` (marked invalid/作废), or `null`
             *     for rows that are still pending/held or that were resolved before the trail existed.
             *     `resolved` remains the boolean form so older filtered pages keep their meaning.
             * @enum {string|null}
             */
            resolution: "attributed" | "completed" | "dismissed" | null;
            /**
             * @description When the human decision was recorded (server clock). `null` for pending/held rows and
             *     for rows resolved before this field existed.
             */
            resolved_at: components["schemas"]["Timestamp"] | null;
            /**
             * @description The platform user id (`sub` of the access token) that recorded the decision, so the
             *     audit trail names an actor rather than only a time. `null` for pending/held rows and for
             *     rows resolved before this field existed.
             */
            resolved_by: components["schemas"]["Uuid"] | null;
            /**
             * @description The submission created by "match user" (`resolution=completed`). Null for pending/held
             *     rows, for dismissals and for the legacy `attributed` decision.
             */
            resolved_submission_id: components["schemas"]["Uuid"] | null;
            /**
             * @description "暂存": reviewed but deliberately deferred. A held row is **not** resolved; it stays
             *     processable and can be resumed (`held=false`).
             */
            held: boolean;
            /** @description When the row was put on hold; null unless `held=true`. */
            held_at: components["schemas"]["Timestamp"] | null;
            /** @description Who put the row on hold; null unless `held=true`. */
            held_by: components["schemas"]["Uuid"] | null;
        };
        /**
         * @description A movable evidence bundle for one evaluation run (CHG-0014). Self-contained on purpose:
         *     a reviewer outside this deployment must be able to verify the decision from the file
         *     alone, so the pack embeds the report and the bindings rather than referencing them by id.
         */
        AppealPack: {
            evaluation_run_id: components["schemas"]["Uuid"];
            /** @description Pins the pack. A reader recomputes it to detect alteration. */
            result_digest: components["schemas"]["Sha256"];
            /** @description The full report for this run, subject to the caller's `view`. */
            report: components["schemas"]["evaluation-report.schema"];
            /** @description The five digests, repeated here so the pack stands alone. Identical digests must reproduce an identical score (ADR-0005). */
            binding: {
                submission_digest: components["schemas"]["Sha256"];
                question_version_binding?: components["schemas"]["Sha256"] | null;
                package_digest: components["schemas"]["Sha256"];
                runner_policy_digest: components["schemas"]["Sha256"];
                environment_digest: components["schemas"]["Sha256"];
            };
            /** @description Which isolation settings were in force, as a summary. Host paths are excluded by construction. */
            runner_policy_summary?: {
                [key: string]: unknown;
            };
            /** @description Human-readable instruction for reproducing this result, so the pack does not depend on the reader knowing this project's runbooks. */
            replay: string;
            /**
             * @description What was removed and why, listed rather than implied. A pack that silently omitted
             *     something would be indistinguishable from one that had nothing to omit -- and the
             *     reader is being asked to trust this file.
             */
            redactions: string[];
            generated_at?: components["schemas"]["Timestamp"] | null;
        };
        /**
         * @description Aggregate quality signal for one question version (CHG-0015). Every field is a count;
         *     there is deliberately no field that could carry an identifier, because the value of an
         *     aggregate is lost the moment it can be walked back to a person.
         */
        EvaluationQualitySummary: {
            question_version_id: components["schemas"]["Uuid"];
            /** @description Distinct students with at least one evaluation run in scope. An aggregate count only: no identifier is exposed, but a teacher needs to distinguish '20 attempts from 2 students' from '20 attempts from 20 students'. */
            answerer_count: number;
            /** @description Evaluation runs in scope. A question with very few attempts has no signal yet -- say so rather than drawing conclusions from noise. */
            attempt_count: number;
            /** @description Per-case failure categories with counts. 'infrastructure_error' is the system's fault and is listed separately so it does not read as difficulty. */
            failure_categories: {
                category: string;
                count: number;
            }[];
            /** @description Which static rules actually fired, and how often. A rule that never fires on any submission is either useless or mis-specified -- that is the point of surfacing it. */
            static_rule_hits: {
                rule_id: string;
                /** @enum {string} */
                severity: "error" | "warning" | "info";
                count: number;
            }[];
            /** @description Per-ordinal failure counts. Ordinals are positions in the immutable version, so this stays comparable across attempts. */
            case_failure_rates: {
                ordinal: number;
                attempts: number;
                failures: number;
            }[];
            /** @description Runs where resource peaks could not be measured. Reported so absence of data is visible as absence, not as zero. */
            unmeasured_runs: number;
            security_found: number;
            security_not_found: number;
            /** @description Never merge into security_not_found: 'we did not look' is not 'we looked and found nothing'. */
            security_not_checked: number;
        };
        /**
         * @description The non-deterministic teaching commentary for one evaluation run (RQ-T-20, ADR-0009),
         *     optionally carrying an advisory score (RQ-T-30, ADR-0014).
         *
         *     This shape still has **no** `score` field and no `evidence` field, and `CHG-0011`'s
         *     reason for that has not gone away: a field named `score`, sitting beside the report,
         *     will eventually be read as the score. What changed (CHG-0018) is that the advisory
         *     score is now modelled *instead of being left to prose*. A commentary that says "this
         *     would probably be full marks under a looser policy" already carries a score-shaped
         *     claim; not modelling it only made the claim unauditable.
         *
         *     So the field exists, and is deliberately **not** a number on this object: it is the
         *     sub-object `advisory_score`, which a consumer can only reach as
         *     `commentary.advisory_score.score` -- a path that states in code what this paragraph
         *     states in prose. The authoritative score lives in the report and in `/grades`; the
         *     separation between the two is enumerated as executable assertions in ADR-0014
         *     decision 3, not promised here.
         */
        EvaluationCommentary: {
            evaluation_run_id: components["schemas"]["Uuid"];
            /**
             * @description `unavailable` = no model provider is configured (the default). `pending` =
             *     generation accepted but not finished. `failed` = generation was attempted and did
             *     not succeed; the score track is unaffected either way.
             * @enum {string}
             */
            status: "unavailable" | "pending" | "ready" | "failed";
            /** @description The commentary itself. Null unless `status` is `ready`. */
            text?: string | null;
            /**
             * @description A model's *suggestion* about how this run might be graded, or null when none was
             *     produced (`status` not `ready`, or the model returned none). Reaching it takes
             *     `commentary.advisory_score.score`: the extra path segment is the point.
             *
             *     It is generated from the same structured evidence as `text`, under the same input
             *     whitelist -- so the model never saw the student's source, and this is a
             *     re-reading of the recorded evidence rather than an independent judgement about
             *     the code. That limit is accepted rather than worked around (ADR-0014 decision 4).
             */
            advisory_score?: components["schemas"]["AdvisoryScore"] | null;
            /** @description Who produced this text. Null when nothing was generated. */
            provenance?: components["schemas"]["CommentaryProvenance"] | null;
            /**
             * @description Always present, never empty -- including when `advisory_score` is null. These are
             *     the normative labels a client must display verbatim alongside the text and the
             *     advisory score: "non-deterministic", "not part of the score", "not grounds for
             *     appeal". A client that renders `advisory_score` without them is presenting a
             *     model's suggestion as a grade.
             */
            disclaimers: string[];
        };
        /**
         * @description A model's advisory estimate of a run's score (RQ-T-30, ADR-0014). It is **not** a score:
         *     it never enters `evaluation_results`, `case_evidence`, `result_digest`, the five binding
         *     digests, the evaluation report, the grades endpoints or any event.
         *
         *     `max_score` is filled by B2 from the structured evidence (the sum of the case weights),
         *     **not** generated. Full marks are a recomputable fact; letting a model produce it would
         *     add a number that can be wrong in a way nobody can see. The model supplies `score`,
         *     `rationale` and `confidence` only.
         */
        AdvisoryScore: {
            /** @description The suggested score. A value below zero or above `max_score` makes the whole generation a failure rather than being clamped -- clamping would silently rewrite what the model said into what we allow it to say. */
            score: number;
            /** @description Sum of the case weights in the recorded evidence. Authoritative, system-filled. */
            max_score: number;
            /** @description Why this estimate, in terms of what the evidence shows. Required so the suggestion can be argued with. */
            rationale: string;
            /**
             * @description The model's own confidence. Advisory like the score itself.
             * @enum {string}
             */
            confidence: "low" | "medium" | "high";
        };
        /**
         * @description The outcome of running a version's own reference answer (CHG-0019). Not persisted by this
         *     endpoint; the attestation is B3's record.
         *
         *     `adapter_kind` repeats the vocabulary B3 declares for a version, and `failure_category`
         *     repeats the evaluation report's -- both on purpose. A private word list here would let the
         *     two sides drift and make a failure category mean one thing in a report and another in a
         *     publish check. (`check_shared_vocabularies` in `MAIN/host/.../checks.py` enforces that the
         *     copies agree.)
         */
        ReferenceVerification: {
            question_version_id: components["schemas"]["Uuid"];
            /** @description The version's digest at the moment of verification. The attestation binds to it, so editing the question invalidates the record. */
            content_digest: components["schemas"]["Sha256"];
            language: components["schemas"]["Language"];
            /**
             * @description Taken from the version's own declaration, never inferred from the reference answer.
             * @enum {string}
             */
            adapter_kind: "stdin" | "file" | "callable";
            cases_total: number;
            cases_passed: number;
            /** @description One entry per case, ordered by `ordinal`. Counts and an outcome only -- no expected output, no hidden-case input, no reference source. */
            cases: {
                ordinal: number;
                /** @description Same per-case shape the evaluation report uses, rather than a second vocabulary for the same fact. */
                passed: boolean;
                /** @description The evaluation report's own failure-category vocabulary. Null exactly when `passed` is true. */
                failure_category: ("timeout" | "crash" | "output_mismatch" | "resource_limit" | "missing_output" | "import_error" | "security_blocked" | "infrastructure_error") | null;
            }[];
            verified_at: components["schemas"]["Timestamp"];
            /** @description The record B3 wrote. Null means recording failed -- the observed `cases` are still returned, because they are what actually happened. */
            attestation_id: components["schemas"]["Uuid"] | null;
        };
        /**
         * @description Which model produced a piece of commentary, and under which prompt. Recorded so that a
         *     later reader can tell whether two commentaries came from the same system, and so a
         *     provider or prompt change is visible rather than invisible.
         */
        CommentaryProvenance: {
            provider: string;
            model: string;
            prompt_version: string;
            /**
             * @description The evidence fields the generation was allowed to read. Returned so the whitelist
             *     is auditable from outside: a reader can see that student source and protected
             *     content were not among the inputs, instead of having to trust that they were not.
             */
            input_scope: string[];
        };
        EvaluationRunView: {
            evaluation_run_id: components["schemas"]["Uuid"];
            submission_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            state: "queued" | "running" | "completed" | "rejected" | "failed";
            /** @enum {string} */
            stage: "queued" | "fetching_package" | "static_analysis" | "security_check" | "dynamic_execution" | "scoring" | "reporting" | "terminal";
            /** @description Monotonic progress counter used by the SSE stream for deduplication. */
            sequence?: number;
            cases_total?: number | null;
            cases_done?: number | null;
            started_at?: components["schemas"]["Timestamp"];
            finished_at?: components["schemas"]["Timestamp"] | null;
        };
        /**
         * @description `deterministic` — the machine score is the grade (default for API submissions);
         *     `awaiting_manual` — only a human can grade this submission, and **no score exists
         *     yet**, so it must not be counted as "not submitted";
         *     `graded` — a manual score exists and is what the student sees.
         * @enum {string}
         */
        GradeStatus: "deterministic" | "awaiting_manual" | "graded";
        /**
         * @description A teacher's score for one submission. `comment` is optional because the product asks
         *     for an optional remark; `reason` is required **only when changing an existing score**,
         *     because a change without a reason is the change that cannot be defended later.
         *     `expected_updated_at` is an optimistic lock: two teachers editing the same grade at
         *     once would otherwise silently overwrite each other.
         */
        ManualGradeRequest: {
            /** @description One decimal place. */
            score: number;
            comment?: string;
            reason?: string;
            expected_updated_at: components["schemas"]["Timestamp"];
        };
        ManualGradeRelease: {
            submission_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            grade_status: "awaiting_manual";
        };
        /**
         * @description The grade as stored. `revision` increments on every change, so a client that saw
         *     revision N knows its write of N+1 landed rather than being overwritten.
         */
        ManualGradeView: {
            submission_id: components["schemas"]["Uuid"];
            score: number;
            /** @enum {string} */
            score_source: "manual";
            grade_status: components["schemas"]["GradeStatus"];
            comment?: string | null;
            graded_by?: components["schemas"]["Uuid"];
            graded_at: components["schemas"]["Timestamp"];
            updated_at: components["schemas"]["Timestamp"];
            revision: number;
        };
        /**
         * @description The message a submission arrived in, for the teacher to read. The body is a **bounded
         *     excerpt** (leading 4000 characters, contact details redacted), never the full message:
         *     the grader only needs enough context to understand what the student meant, and storing
         *     whole mailboxes would turn the platform into an archive of unrelated correspondence.
         */
        IntakeMessageView: {
            subject: string | null;
            sender: string;
            received_at: components["schemas"]["Timestamp"];
            body_excerpt: string;
            /** @default false */
            truncated: boolean;
            attachment_names?: string[];
        };
        /**
         * @description One grade record. `score` is null while the run is unfinished; the row then
         *     reports its `state` instead, so an incomplete evaluation is never displayed
         *     as a zero.
         *
         *     `student_number` / `display_name` stay null on this read path: B2 owns only the
         *     `b2` schema and must not read B4's tables. The P11 page joins these rows with the
         *     authorized roster (`GET /sections/{section_id}/enrollments`) by `student_id`, and
         *     the export path asks B4 through that same authorized interface — neither invents
         *     a value for a row that is missing from the roster.
         *
         *     `security_status` is the three-state conclusion of the run that produced the score
         *     (`found` / `not_found` / `not_checked`); null means the run never reached the
         *     security stage. `not_checked` must never be folded into `not_found`.
         */
        GradeRow: {
            submission_id: components["schemas"]["Uuid"];
            student_id: components["schemas"]["Uuid"];
            student_number?: string | null;
            /** @description Shown only to callers authorized for the section. */
            display_name?: string | null;
            course_id?: components["schemas"]["Uuid"];
            section_id?: components["schemas"]["Uuid"];
            /** @description The assignment a submission answers (CHG-0032). Null only for historical rows accepted before the field existed; they are never guessed into an assignment. */
            assignment_id?: components["schemas"]["Uuid"] | null;
            question_version_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            state: "received" | "validated" | "queued" | "running" | "completed" | "rejected" | "failed";
            score?: number | null;
            max_score?: number | null;
            is_pass?: boolean | null;
            grade_status?: components["schemas"]["GradeStatus"];
            /**
             * @description Where the score came from. `deterministic` is recomputable from the submission
             *     and the cases; `manual` is a teacher's judgement and carries an author. Null while
             *     no score exists. Without this field an export cannot answer "who produced this
             *     number", which is the first question a disputed grade raises.
             */
            score_source?: ("deterministic" | "manual") | null;
            submitted_at?: components["schemas"]["Timestamp"];
            evaluation_run_id?: components["schemas"]["Uuid"] | null;
            security_status?: ("found" | "not_found" | "not_checked") | null;
        };
        /**
         * @description One step of a layered learning hint. The levels are ordered from the gentlest
         *     ("what concept is involved") to the most specific ("how to check your own work"), so a
         *     client can reveal them progressively. Progressive disclosure is a learning-pace control,
         *     not a security boundary: every returned step has already passed the safe-output guard.
         */
        LearningHintStep: {
            /** @enum {string} */
            level: "knowledge" | "direction" | "self_check";
            title: string;
            /** @description Teaching text. Short pseudocode or a check list is allowed; a complete runnable answer is rejected server-side. */
            text: string;
        };
        /**
         * @description The student's own layered hints for one `evaluation_run` (AI-FLOW-01B).
         *
         *     This shape carries **no** score, no case evidence, no expected output and no
         *     `advisory_score`: hints are a learning aid and must never become a second grading track.
         *     `hint_steps` is an ordered list of one to three steps; a useful run may legitimately yield
         *     fewer than three, and the list is empty exactly when `status` is not `ready`.
         *
         *     `provider` / `model` / `prompt_version` are the provenance of a generated result (null when
         *     nothing was generated); `context_scope` lists the fields the model was allowed to read, so a
         *     reader can verify that student source and hidden cases were not among them.
         */
        LearningHintsResponse: {
            evaluation_run_id: components["schemas"]["Uuid"];
            /** @description Stable id of the stored hint row; null when no row exists (`not_requested` / `not_applicable` / `unavailable`). */
            generation_id?: components["schemas"]["Uuid"] | null;
            /**
             * @description `not_requested` = provider available but nothing generated for this run yet.
             *     `not_applicable` = completed run without deterministic failure evidence to explain.
             *     `pending` = generation accepted but not finished.
             *     `ready` = steps are present.
             *     `failed` = generation was attempted and did not produce a safe, parseable result.
             *     `unavailable` = no provider is configured (and no earlier result exists).
             * @enum {string}
             */
            status: "not_requested" | "not_applicable" | "pending" | "ready" | "failed" | "unavailable";
            hint_steps: components["schemas"]["LearningHintStep"][];
            provider?: string | null;
            model?: string | null;
            prompt_version?: string | null;
            /** @description Evidence/public-context fields the generation was allowed to read. Empty when nothing was generated. */
            context_scope?: string[];
            /** @description Redacted category of a failed generation (e.g. provider_error, invalid_model_output, unsafe_output). No model text is stored or returned. */
            failure_category?: string | null;
            /** @description Normative labels that must be displayed with the steps: model-generated, not part of the score, no complete answer. */
            disclaimers: string[];
        };
        /**
         * @description Which assignment the student is about to prove ownership of an e-mail address for. A
         *     challenge is assignment-scoped because one message may carry one `.txt` answer per
         *     question. `course_id`/`section_id` remain in the body for contract stability, but the
         *     server does **not** trust them: it resolves the caller's current enrollment from B4 and
         *     binds the challenge to the authoritative course/section (v0.32.0). A request whose
         *     course does not match the assignment is refused.
         */
        EmailVerificationChallengeRequest: {
            course_id: components["schemas"]["Uuid"];
            section_id: components["schemas"]["Uuid"];
            assignment_id: components["schemas"]["Uuid"];
        };
        /**
         * @description The one-time code, returned once at creation. The server stores only its SHA-256, so a
         *     database read cannot recover it; it is never logged and never appears in an event.
         */
        EmailVerificationChallenge: {
            challenge_id: components["schemas"]["Uuid"];
            assignment_id: components["schemas"]["Uuid"];
            /** @description Single-use ownership proof the student puts in the message body. */
            code: string;
            expires_at: components["schemas"]["Timestamp"];
        };
        /**
         * @description Student-visible state of the ownership proof. `pending` means no message carrying this
         *     code has been read yet; `received` means a real IMAP message was bound and the mapped
         *     per-question submissions were created; `rejected` carries a stable reason code; `expired`
         *     means the code can no longer bind a message. The response never echoes the code, the
         *     address, the subject or any message body.
         */
        EmailVerificationChallengeStatus: {
            challenge_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            state: "pending" | "received" | "rejected" | "expired";
            /**
             * @description Stable machine code; null unless state is rejected. `intake_unavailable` is a
             *     platform condition, not a student mistake. `email_body_missing_field` and
             *     `email_body_mismatch` mean the message body lacked a required labelled field or its
             *     assignment/student identity disagreed with the authenticated challenge;
             *     `attachment_mapping_invalid` means the attachments could not be mapped to the
             *     assignment's questions (name must be `<n>.txt` with n a 1-based question position).
             *     `subject_mismatch` and `attachment_missing` are reserved legacy values and are no
             *     longer produced by intake; the per-case evaluation reports the corresponding mismatch.
             * @enum {string|null}
             */
            reason_code?: "subject_mismatch" | "attachment_missing" | "email_sender_mismatch" | "email_verification_rejected" | "email_verification_expired" | "email_body_missing_field" | "email_body_mismatch" | "attachment_mapping_invalid" | "intake_unavailable" | null;
            expires_at: components["schemas"]["Timestamp"];
            verified_at?: components["schemas"]["Timestamp"] | null;
            /**
             * @description The submissions this message created, one per mapped question (CHG-0032). Empty
             *     while pending or when the message did not become a submission. The order is the
             *     assignment's question order.
             */
            submissions?: {
                question_version_id: components["schemas"]["Uuid"];
                submission_id: components["schemas"]["Uuid"];
            }[];
            /** @description The assignment this challenge belongs to; null only for historical challenges issued before CHG-0032. */
            assignment_id?: components["schemas"]["Uuid"] | null;
        };
        file: {
            /** @description Single-segment .txt answer file. In an assignment, the file name is the 1-based question ordinal: 1.txt, 2.txt, ... */
            path: string;
            content_sha256: string;
            content_base64: string;
        };
        "submission-envelope.schema": {
            /** Format: uuid */
            submission_id: string;
            /** Format: uuid */
            assignment_id: string;
            /** Format: uuid */
            student_id: string;
            /** Format: uuid */
            course_id: string;
            /** Format: uuid */
            section_id: string;
            /** Format: uuid */
            question_version_id: string;
            /** @description One submission is one assignment question and accepts exactly one .txt answer file (CHG-0032). */
            files: components["schemas"]["file"][];
            idempotency_key: string;
            $defs: {
                file: {
                    /** @description Single-segment .txt answer file. In an assignment, the file name is the 1-based question ordinal: 1.txt, 2.txt, ... */
                    path: string;
                    content_sha256: string;
                    content_base64: string;
                };
            };
        };
        /**
         * Format: uuid
         * @description UUID v4 (RFC 9562), generated by the owning module. See MAIN/docs/api/identifiers.md.
         */
        Uuid: string;
        /**
         * Format: date-time
         * @description UTC RFC 3339 with millisecond precision, e.g. 2026-09-15T08:30:00.000Z.
         */
        Timestamp: string;
        /**
         * @description RFC 9457 problem details. Never carries stack traces, SQL or internal paths.
         *
         *     **The status code's meaning is a hard constraint; the order between competing codes is
         *     not.** Each code keeps exactly one meaning (`governance.md` section 2.2): `401` is
         *     "not authenticated", `403` is "authenticated but not permitted", `404` is "permitted but
         *     absent", `422` is "well-formed but semantically invalid". That part is guaranteed.
         *
         *     When more than one code applies to the same request, **their relative order is not
         *     guaranteed and consumers must handle each independently.** This is stated rather than
         *     left implicit because modules genuinely differ here and both ways are defensible: B2 and
         *     B4 authenticate before validating (B2 defers parameter parsing to its service layer so
         *     that it has a single Problem Details producer), while B3 lets the framework validate
         *     first. See `CHG-0021` section 2.6 for the measurements. A client that assumed one order
         *     would break against the other module.
         *
         *     Two things *are* ordered and may be relied on:
         *
         *     * **Route existence and method matching (`404` for an unknown path, `405` for a known
         *       path with the wrong method) precede both `401` and `422`.** They are routing-layer
         *       outcomes and depend on no request content. This leaks nothing new: which methods exist
         *       on which paths is written in this contract.
         *     * **`403`, `404` (resource absent) and `409` never compete with `401`** -- they are the
         *       conclusions of an *authenticated* request.
         */
        ProblemDetails: {
            /**
             * Format: uri-reference
             * @example https://autograder.local/problems/forbidden
             */
            type: string;
            /** @example Forbidden */
            title: string;
            status: number;
            detail: string;
            /** @description Request path or trace identifier */
            instance: string;
        };
        /** @description Lowercase hex SHA-256 digest. */
        Sha256: string;
        /** @description Workspace-relative paths, sorted, so the list is comparable between runs. Bounded because a runaway case must not produce an unbounded report. */
        file_change_list: {
            path: string;
            content_sha256?: string | null;
            size_bytes?: number | null;
        }[];
        /** @description Peak resource use observed for one case. Every field is nullable because a sandbox may be able to enforce a limit without being able to report the peak; 'enforced but unmeasured' and 'measured as small' are different facts. */
        case_resource: {
            memory_peak_bytes?: number | null;
            cpu_user_seconds?: number | null;
            cpu_system_seconds?: number | null;
            pids_peak?: number | null;
            wall_seconds?: number | null;
            /**
             * @description Which enforced limit ended the case, if any. Null means no limit was hit.
             * @enum {string|null}
             */
            limit_hit?: "memory" | "cpu" | "pids" | "wall" | "output" | null;
        };
        /** @description File-level outcome of one case. Digests are recorded so the comparison is checkable after the fact; the change lists are recorded so `RQ-B-0-30` acceptance 2 ('which files the submission modified, with content digests') is answerable at all. */
        case_artifacts: {
            /** @description PROTECTED. Digest of the expected produced file. Omitted from the student view: a digest is not content, but it is a checkable oracle for a guessed file, and only visible cases may expose it. */
            expected_sha256?: string | null;
            actual_sha256?: string | null;
            /** @description Decisions made directly on files after the run (single_run `file_checks`). Recorded here so 'the file content/permission bits were checked' is auditable after the fact rather than merely claimed in an explanation. */
            file_checks?: {
                path: string;
                expected_sha256?: string | null;
                actual_sha256?: string | null;
                expected_mode?: string | null;
                actual_mode?: string | null;
                matched: boolean;
            }[];
            created?: components["schemas"]["file_change_list"];
            modified?: components["schemas"]["file_change_list"];
            deleted?: components["schemas"]["file_change_list"];
        };
        /**
         * Evaluation report
         * @description Explainable, replayable evaluation report produced by B2 (RQ-B-2-80). A score is not a scalar here: it is a set of per-rule and per-case conclusions, each carrying evidence, so a third party can re-check it. The report is bound to five digests; identical digests must reproduce an identical score and identical per-item evidence (ADR-0005).
         */
        "evaluation-report.schema": {
            /** Format: uuid */
            evaluation_run_id: string;
            /** Format: uuid */
            submission_id: string;
            /** Format: uuid */
            question_version_id: string;
            /**
             * @description Terminal state. 'rejected' means the submission broke a rule or a security policy; 'failed' means the system could not complete the evaluation. Neither may be presented as a low score.
             * @enum {string}
             */
            state: "completed" | "rejected" | "failed";
            /** @description Overall assessment: score, full marks, pass flag and a conclusion summary. */
            overall: {
                score: number;
                max_score: number;
                is_pass: boolean;
                /** @description Deterministic, rule-derived conclusion text. Not model-generated: scoring never depends on an LLM (ADR-0005). */
                summary?: string | null;
            };
            /** @description How the cases were produced. `invocation_count` is the number of sandbox executions actually started (per_case = number of cases, single_run/ssh_session = 1, email_delivery = 0 because no code is executed). It is recorded separately from `evidence_count` so a single-run question cannot be reported as if each case had its own sandbox. */
            execution?: {
                /** @enum {string} */
                mode: "per_case" | "single_run" | "ssh_session" | "email_delivery";
                invocation_count: number;
                evidence_count: number;
            };
            /** @description Static legality findings (RQ-B-2-40). A high-severity finding blocks dynamic execution and makes the outcome 'rejected'; in that case the report must not claim the code ran successfully. */
            static_findings: {
                rule_id: string;
                /** @enum {string} */
                severity: "error" | "warning" | "info";
                file: string;
                line?: number | null;
                column?: number | null;
                message: string;
                /**
                 * @description Whether this finding blocked dynamic execution.
                 * @default false
                 */
                blocking: boolean;
            }[];
            /** @description Three-state security conclusion. 'not_checked' is distinct from 'not_found': an unperformed check must never be reported as a clean result. */
            security_conclusion: {
                /** @enum {string} */
                status: "found" | "not_found" | "not_checked" | "not_applicable";
                findings: {
                    /** Format: uuid */
                    finding_id: string;
                    /** @enum {string} */
                    category: "destructive_command" | "network_attempt" | "privilege_escalation" | "sensitive_path_read" | "fork_bomb" | "resource_limit" | "archive_bomb" | "sandbox_violation";
                    /** @enum {string} */
                    severity: "high" | "medium" | "low";
                    /** @enum {string} */
                    detected_in?: "static" | "runtime";
                    message?: string;
                }[];
            };
            /** @description Per-case evidence (RQ-B-2-50). Reports digests and truncated excerpts rather than whole outputs, and never leaks a hidden case's expected output to the student view. */
            case_evidence: {
                /** Format: uuid */
                case_id: string;
                ordinal: number;
                /** @description For a single_run package, which delimiter-separated stdout segment this evidence describes. Null for other modes and for file/ssh/email checks. */
                segment?: number | null;
                /** @default 1 */
                weight: number;
                awarded?: number;
                passed: boolean;
                /** @enum {string|null} */
                failure_category?: "timeout" | "crash" | "output_mismatch" | "resource_limit" | "missing_output" | "import_error" | "security_blocked" | "infrastructure_error" | null;
                actual_output_digest?: string | null;
                /** @description Truncated per the output limit. Omitted entirely for hidden cases in the student view. */
                actual_output_excerpt?: string | null;
                /** @description PROTECTED. Omitted for hidden cases; only visible cases may expose it. */
                expected_output_excerpt?: string | null;
                duration_ms?: number | null;
                message?: string | null;
                /** @description Measured resource use for this case (CHG-0012). Null when the host could not measure it -- e.g. no cgroup v2 -- which is honest ('not measured') rather than zero, because a zero reads as 'used nothing'. Before this field the resource ledger was a set of configured limits nobody ever compared against reality. */
                resource?: components["schemas"]["case_resource"] | null;
                /** @description What this case did to the workspace files (CHG-0012). Closes a gap the integration audit found: per-case file digests were computed, then used only to pick an excerpt and discarded, so 'the produced file matched' was unauditable and `RQ-B-0-30` acceptance 2 had no implementation anywhere. */
                artifacts?: components["schemas"]["case_artifacts"] | null;
                /** @description A deterministic, rule-derived explanation of this case's outcome (CHG-0012). It is NOT model output: fixed rules over the evidence in this same object, so it is reproducible and may be treated as part of the report. Model-generated teaching commentary lives in a separate track (ADR-0009, RQ-T-20) and must never appear here -- if it did, the report would stop being replayable. */
                explanation?: string | null;
            }[];
            /** @description The five digests that make replay meaningful. When all five match a previous run, the score and per-item evidence must match too; recording them separately also makes it possible to say exactly which input changed when a result legitimately differs. */
            binding: {
                submission_digest: string;
                /** @description Content digest of the question version, so a later edit to the question cannot silently change the meaning of a past score. */
                question_version_binding?: string | null;
                package_digest: string;
                runner_policy_digest: string;
                environment_digest: string;
                interpreter_version?: string | null;
            };
            /** @description Arithmetic self-check. The sum of item awards must equal the overall score; a mismatch is a defect that must fail a test, not be rounded away. */
            total_check: {
                sum_of_awards: number;
                /** @constant */
                matches_total: true;
            };
            /** @description Digest over the report content, used as the replay equality check. */
            result_digest: string;
            /** Format: date-time */
            generated_at?: string | null;
            $defs: {
                /** @description Peak resource use observed for one case. Every field is nullable because a sandbox may be able to enforce a limit without being able to report the peak; 'enforced but unmeasured' and 'measured as small' are different facts. */
                case_resource: {
                    memory_peak_bytes?: number | null;
                    cpu_user_seconds?: number | null;
                    cpu_system_seconds?: number | null;
                    pids_peak?: number | null;
                    wall_seconds?: number | null;
                    /**
                     * @description Which enforced limit ended the case, if any. Null means no limit was hit.
                     * @enum {string|null}
                     */
                    limit_hit?: "memory" | "cpu" | "pids" | "wall" | "output" | null;
                };
                /** @description File-level outcome of one case. Digests are recorded so the comparison is checkable after the fact; the change lists are recorded so `RQ-B-0-30` acceptance 2 ('which files the submission modified, with content digests') is answerable at all. */
                case_artifacts: {
                    /** @description PROTECTED. Digest of the expected produced file. Omitted from the student view: a digest is not content, but it is a checkable oracle for a guessed file, and only visible cases may expose it. */
                    expected_sha256?: string | null;
                    actual_sha256?: string | null;
                    /** @description Decisions made directly on files after the run (single_run `file_checks`). Recorded here so 'the file content/permission bits were checked' is auditable after the fact rather than merely claimed in an explanation. */
                    file_checks?: {
                        path: string;
                        expected_sha256?: string | null;
                        actual_sha256?: string | null;
                        expected_mode?: string | null;
                        actual_mode?: string | null;
                        matched: boolean;
                    }[];
                    created?: components["schemas"]["file_change_list"];
                    modified?: components["schemas"]["file_change_list"];
                    deleted?: components["schemas"]["file_change_list"];
                };
                /** @description Workspace-relative paths, sorted, so the list is comparable between runs. Bounded because a runaway case must not produce an unbounded report. */
                file_change_list: {
                    path: string;
                    content_sha256?: string | null;
                    size_bytes?: number | null;
                }[];
            };
        };
        /** @description Cursor pagination envelope. Offset pagination is forbidden for large tables. */
        CursorPage: {
            items: unknown[];
            next_cursor?: string | null;
        };
        /**
         * @description Graded language. Required by RQ-B-0-10; B2 selects the interpreter from this value.
         * @enum {string}
         */
        Language: "python" | "bash";
    };
    responses: {
        /** @description Missing, malformed or expired credentials. Does not reveal whether the principal exists. */
        Unauthorized: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description Authenticated, but the role or course scope does not permit the operation. */
        Forbidden: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description Authenticated and authorized, but the resource does not exist. */
        NotFound: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description File, aggregate or attachment size limit exceeded. */
        PayloadTooLarge: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description Schema-valid request rejected by a business rule. */
        UnprocessableEntity: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description A required dependency is unavailable; the response names the dependency. */
        ServiceUnavailable: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description State conflict, e.g. an immutable version, a referenced version or a concurrent edit. */
        Conflict: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
    };
    parameters: {
        /** @description W3C Trace Context. Propagated to logs, database records and emitted events. */
        Traceparent: string;
        /** @description Correlation identifier. Generated at the entry point when absent. */
        RequestId: string;
        /** @description Alternative to a body-carried idempotency key for create operations. */
        IdempotencyKey: string;
        Cursor: string;
        Limit: number;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    createSubmission: {
        parameters: {
            query?: never;
            header?: {
                /** @description W3C Trace Context. Propagated to logs, database records and emitted events. */
                traceparent?: components["parameters"]["Traceparent"];
                /** @description Correlation identifier. Generated at the entry point when absent. */
                "X-Request-ID"?: components["parameters"]["RequestId"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["submission-envelope.schema"];
            };
        };
        responses: {
            /** @description Submission accepted and queued for evaluation. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubmissionAccepted"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            413: components["responses"]["PayloadTooLarge"];
            422: components["responses"]["UnprocessableEntity"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    getSubmission: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submission_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Submission view. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SubmissionView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    getEvaluationRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Evaluation run view. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EvaluationRunView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    streamEvaluationEvents: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /**
             * @description SSE stream. Frame `event` names are exactly `progress`, `completed`,
             *     `rejected` and `failed`; each frame's `data` is an EvaluationStreamFrame.
             *     This stream — not a NATS subscription — is how the B1 client learns every
             *     terminal state, including `rejected`, which has no domain event because a
             *     refusal is only meaningful to the submitting session.
             */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": string;
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    getEvaluationReport: {
        parameters: {
            query?: {
                /** @description The student view is redacted further; the server decides what a role may see. */
                view?: "student" | "teacher";
            };
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Evaluation report. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["evaluation-report.schema"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    getEvaluationCommentary: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Commentary, or an explicit statement that none is configured/generated. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EvaluationCommentary"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    requestEvaluationCommentary: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Generation accepted; read it back with `getEvaluationCommentary`. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EvaluationCommentary"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    getLearningHints: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Hints, or an explicit statement of why none are available. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LearningHintsResponse"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    requestLearningHints: {
        parameters: {
            query?: never;
            header?: {
                /** @description Alternative to a body-carried idempotency key for create operations. */
                "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Stored result (`ready` / `failed`) or `not_applicable`. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LearningHintsResponse"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
            /** @description The deployment-configured generation budget for this student is exhausted. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    listIntakeQuarantines: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                course_id?: components["schemas"]["Uuid"];
                section_id?: components["schemas"]["Uuid"];
                reason_code?: string;
                /**
                 * @description `true` returns only rows the reviewer put on hold ("暂存"), `false` excludes held rows.
                 *     Omitting it filters neither. Held rows are still unresolved and remain processable.
                 */
                held?: boolean;
                /**
                 * @description `true` returns only processed records (attributed **or** dismissed), `false` only
                 *     unprocessed ones.
                 *
                 *     **Omitting it means no filter at all** -- both kinds, in one page. This is stated
                 *     explicitly because the previous wording ("the default view a teacher wants") read like
                 *     "omitting means unresolved", and `CHG-0021` §2.5 settled it the other way for a reason
                 *     of expressiveness rather than taste: the type is `boolean`, so it has no value meaning
                 *     "both". If omitting meant `false`, then *listing everything* would have no request form
                 *     at all and the capability would be permanently gone. "Only unattributed" is always
                 *     expressible as `?resolved=false`; "everything" is not expressible any other way.
                 */
                resolved?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of quarantined intake records, newest first. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage"] & {
                        items?: components["schemas"]["IntakeQuarantineView"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    resolveIntakeQuarantine: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    student_id: components["schemas"]["Uuid"];
                    /** @description Why this attribution, for the audit trail. */
                    note?: string | null;
                };
            };
        };
        responses: {
            /** @description Attributed; the record is marked resolved and the attribution is audited. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntakeQuarantineView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    dismissIntakeQuarantine: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /** @description Why this dismissal, for the audit trail. */
                    note?: string | null;
                };
            };
        };
        responses: {
            /** @description Dismissed; the record is marked resolved and the decision is audited. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntakeQuarantineView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    setIntakeQuarantineHold: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    held: boolean;
                };
            };
        };
        responses: {
            /** @description Hold state updated; the record itself is still unresolved. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntakeQuarantineView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    completeIntakeQuarantine: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                intake_quarantine_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    student_id: components["schemas"]["Uuid"];
                    course_id: components["schemas"]["Uuid"];
                    section_id: components["schemas"]["Uuid"];
                    assignment_id: components["schemas"]["Uuid"];
                    note?: string | null;
                };
            };
        };
        responses: {
            /** @description Submission created and the record marked completed. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntakeQuarantineView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    verifyReferenceAnswer: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    question_version_id: components["schemas"]["Uuid"];
                };
            };
        };
        responses: {
            /**
             * @description The reference answer was executed and compared. `cases_passed == cases_total` means
             *     the version is publishable; `attestation_id` is present only if B3 recorded it.
             */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReferenceVerification"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            422: components["responses"]["UnprocessableEntity"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    getAppealPack: {
        parameters: {
            query?: {
                /** @description Same discipline as the report endpoint: the server decides what a role may see. */
                view?: "student" | "teacher";
            };
            header?: never;
            path: {
                evaluation_run_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The bundle. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AppealPack"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    getEvaluationQualitySummary: {
        parameters: {
            query: {
                question_version_id: components["schemas"]["Uuid"];
                from?: components["schemas"]["Timestamp"];
                to?: components["schemas"]["Timestamp"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Aggregate. Counts only; never an individual's data. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EvaluationQualitySummary"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    getManualGrade: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submission_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The stored manual grade. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ManualGradeView"];
                };
            };
            /** @description No manual grade exists yet. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    setManualGrade: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submission_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ManualGradeRequest"];
            };
        };
        responses: {
            /** @description Grade stored (created or updated). */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ManualGradeView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            /**
             * @description The grade changed since `expected_updated_at` (someone else graded it first), or a
             *     score change arrived without a `reason`.
             */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    releaseManualGrade: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submission_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Score withdrawn; `grade_status` is `awaiting_manual` again. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ManualGradeRelease"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            /** @description No manual score exists to withdraw. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    downloadSubmissionFile: {
        parameters: {
            query: {
                /**
                 * @description The submission-relative path recorded in `submission_files.path`. A submission is one
                 *     assignment question and carries exactly one `.txt` answer file named after the
                 *     question's 1-based position in the assignment (`1.txt`, `2.txt`, ...). It is a path
                 *     rather than an id because that table is keyed by `(submission_id, path)`.
                 */
                path: string;
            };
            header?: never;
            path: {
                submission_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description File bytes. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/octet-stream": string;
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    getSubmissionIntakeMessage: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                submission_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The intake message. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IntakeMessageView"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    listGrades: {
        parameters: {
            query: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                course_id: components["schemas"]["Uuid"];
                section_id?: components["schemas"]["Uuid"];
                question_version_id?: components["schemas"]["Uuid"];
                /** @description Narrow the page to one assignment (CHG-0032). Rows accepted before assignment_id existed have no assignment and are therefore returned by no assignment-scoped query. */
                assignment_id?: components["schemas"]["Uuid"];
                student_id?: components["schemas"]["Uuid"];
                /** @description Inclusive lower bound on the submission time (RFC 3339, UTC). Same semantics as the export filter. */
                from?: components["schemas"]["Timestamp"];
                /** @description Inclusive upper bound on the submission time (RFC 3339, UTC). Must not be earlier than `from` (422). */
                to?: components["schemas"]["Timestamp"];
                /** @description Narrow the page to the submission that owns this evaluation run. Used by the P10 -> P11 result deep link; the caller's normal course/student scope still applies, so another student's run id yields an empty page rather than a wider read. */
                evaluation_run_id?: components["schemas"]["Uuid"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of grade rows. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage"] & {
                        items?: components["schemas"]["GradeRow"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    exportGrades: {
        parameters: {
            query: {
                course_id: components["schemas"]["Uuid"];
                section_id?: components["schemas"]["Uuid"];
                /** @description Same assignment filter as `listGrades` (CHG-0032). */
                assignment_id?: components["schemas"]["Uuid"];
                question_version_id?: components["schemas"]["Uuid"];
                student_id?: components["schemas"]["Uuid"];
                /** @description Same inclusive lower bound as `listGrades`: the exported rows are exactly the rows the page showed. */
                from?: components["schemas"]["Timestamp"];
                /** @description Same inclusive upper bound as `listGrades`. */
                to?: components["schemas"]["Timestamp"];
                /** @description Same run filter as `listGrades`. */
                evaluation_run_id?: components["schemas"]["Uuid"];
                /**
                 * @description Comma-separated optional column selection. Unknown names are rejected with 422 rather
                 *     than ignored. The required columns of RQ-B-1-60 (student number, name, course, section,
                 *     question version, score, max score, submitted time, evaluation run id) are **always**
                 *     present and cannot be removed; the file is produced by B2 with B4-supplied names and
                 *     student numbers, and the export fails rather than emitting a file whose identities are
                 *     missing.
                 */
                columns?: string;
                format?: "xlsx" | "csv";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Generated export file. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": string;
                    "text/csv": string;
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    createEmailVerificationChallenge: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmailVerificationChallengeRequest"];
            };
        };
        responses: {
            /** @description The one-time code. It is shown once and never logged. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EmailVerificationChallenge"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getEmailVerificationChallengeStatus: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                challenge_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Current state, with a stable reason code when rejected. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EmailVerificationChallengeStatus"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
}
