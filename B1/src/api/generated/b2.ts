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
         *     count and size limits, checks the course scope from the verified token, and
         *     stores content by digest. Repeating a request with the same idempotency key
         *     returns the same submission_id and does not start a second evaluation.
         *
         *     On success emits `autograder.v1.submission.accepted`; the event never carries
         *     source code.
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
         * @description Teacher/assistant only: generating commentary is an editorial act, and `ADR-0009`
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
         *     Authorization: teacher/assistant/admin within the course scope. Rows carry a
         *     `reason_code` and an already-redacted `detail`; they never carry attachment contents,
         *     student source or any part of a submission.
         *
         *     **Scoping is per token, not per row** (`CHG-0021` §2.2). `b2.intake_quarantines` has no
         *     `course_id`, and when matching fails there is no course to record -- working out "which
         *     course is this" is exactly what failed. So this endpoint requires the token to carry at
         *     least one course scope and **cannot** filter rows by course. Stated here rather than left
         *     to be discovered: a caller that assumed per-row scoping would read a superset.
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
         *        silently re-attributed.
         *
         *     `student_id` is the platform user id -- the same key `CHG-0007` added to the roster, so
         *     both sides of "who is this" finally use one identifier.
         */
        post: operations["resolveIntakeQuarantine"];
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
         *     Authorization: teacher/assistant/admin, plus the student for their own run.
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
         * @description One quarantined intake record (CHG-0014). Carries identification metadata and a
         *     redacted reason, never message contents: the point is that a teacher can *find* the
         *     message in their mailbox again, not that the platform becomes a second mail store.
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
            reason_code: "not_uniquely_matched" | "ambiguous_match" | "malformed_message" | "missing_message_id" | "no_attachments" | "too_many_attachments" | "message_too_large" | "attachment_name_not_allowed" | "duplicate_attachment_name" | "unsupported_attachment_encoding" | "malformed_attachment" | "attachment_too_large" | "attachment_total_too_large" | "unsupported_attachment_type" | "nested_message_attachment" | "executable_attachment" | "archive_bomb" | "archive_attachment" | "not_plain_text";
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
         * @description One grade record. `score` is null while the run is unfinished; the row then
         *     reports its `state` instead, so an incomplete evaluation is never displayed
         *     as a zero.
         */
        GradeRow: {
            submission_id: components["schemas"]["Uuid"];
            student_id: components["schemas"]["Uuid"];
            student_number?: string | null;
            /** @description Shown only to callers authorized for the section. */
            display_name?: string | null;
            course_id?: components["schemas"]["Uuid"];
            section_id?: components["schemas"]["Uuid"];
            question_version_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            state: "received" | "validated" | "queued" | "running" | "completed" | "rejected" | "failed";
            score?: number | null;
            max_score?: number | null;
            is_pass?: boolean | null;
            submitted_at?: components["schemas"]["Timestamp"];
            evaluation_run_id?: components["schemas"]["Uuid"] | null;
        };
        file: {
            path: string;
            content_sha256: string;
            content_base64: string;
        };
        "submission-envelope.schema": {
            /** Format: uuid */
            submission_id: string;
            /** Format: uuid */
            student_id: string;
            /** Format: uuid */
            course_id: string;
            /** Format: uuid */
            section_id: string;
            /** Format: uuid */
            question_version_id: string;
            files: components["schemas"]["file"][];
            idempotency_key: string;
            $defs: {
                file: {
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
                status: "found" | "not_found" | "not_checked";
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
    listIntakeQuarantines: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                reason_code?: string;
                /**
                 * @description `true` returns only attributed records, `false` only unattributed ones.
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
    listGrades: {
        parameters: {
            query: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                course_id: components["schemas"]["Uuid"];
                section_id?: components["schemas"]["Uuid"];
                question_version_id?: components["schemas"]["Uuid"];
                student_id?: components["schemas"]["Uuid"];
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
                question_version_id?: components["schemas"]["Uuid"];
                student_id?: components["schemas"]["Uuid"];
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
}
