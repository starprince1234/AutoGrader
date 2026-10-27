/**
 * AUTO-GENERATED FROM openapi/b3.yaml — DO NOT EDIT BY HAND.
 *
 * Regenerate with: pnpm api:generate
 * Source of truth: MAIN/contracts/** (see docs/adr/ADR-0002-contract-generated-api-client.md).
 * Any hand edit is reported as contract drift by `pnpm api:check`.
 */

export interface paths {
    "/api/v1/questions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List questions in the caller's course scope
         * @description Returns the public question DTO only. Asserted by contract test: the response
         *     must not contain reference_answer, hidden cases or expected_output.
         */
        get: operations["listQuestions"];
        put?: never;
        /**
         * Create a question with a first draft version
         * @description `course_id` is a required query parameter, not a body field: a question is created
         *     inside a course scope and `QuestionDraftInput` is closed
         *     (`additionalProperties: false`), so the scope travels beside the body rather than
         *     inside it. This was declared after `CHG-B3-0002`; the parameter itself has been
         *     accepted by the implementation since the question bank phase.
         */
        post: operations["createQuestion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/coverage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Per-tag question counts for one course (RQ-B-3-20, CHG-0020)
         * @description `tags` can be written (CHG-0008) and searched (CHG-0013), but their **distribution** had no
         *     outlet: `CursorPage` carries only `next_cursor` -- no total -- and counting by paging is not
         *     something a reader can do. So "does this topic have two questions or twenty?" had no answer
         *     short of walking the bank by hand.
         *
         *     **Counts only.** The response deliberately carries no `question_id` list: with ids it stops
         *     being a distribution and becomes a way to walk the bank one question at a time. Same reason
         *     `CHG-0015`'s quality summary is counts-only.
         *
         *     Declared **before** `/questions/{question_id}` on purpose: it is a literal segment under a
         *     collection that also has a parameterised child, and a router that declares the
         *     parameterised route first will swallow it (the same trap `quality-summary` documents).
         */
        get: operations["getQuestionTagCoverage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/{question_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /** Read a question and its version history (public DTO) */
        get: operations["getQuestion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update the mutable draft of a question
         * @description Only drafts are mutable. Editing a published version returns 409 unless
         *     `as_new_version=true` is passed, in which case the edit produces a **new** version
         *     and the published one stays immutable -- the requirement is that a published version
         *     is never edited in place, not that editing is forbidden.
         */
        patch: operations["updateQuestionDraft"];
        trace?: never;
    };
    "/api/v1/questions/{question_id}/assets": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Upload a stem graphic for a question (RQ-B-3-10)
         * @description Only a question whose current version is still a draft accepts a new graphic: a
         *     published version is immutable, and its stem already references the assets that
         *     existed when it was published.
         *
         *     The server computes `content_sha256`; a digest supplied by the caller is never
         *     trusted as authoritative. `content_type` is restricted to an image allow-list, so
         *     a stored asset can never be served as a document.
         */
        post: operations["uploadQuestionAsset"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/{question_id}/assets/{asset_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
                asset_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Read a stem graphic over the controlled asset path (RQ-B-3-10 acceptance 5)
         * @description Returns the raw image bytes. Authorization is evaluated per request: an asset that
         *     exists but belongs to a question outside the caller's course scope is answered
         *     with 403, and a request without a usable token with 401. An `asset_id` that does
         *     not exist is 404 — asset ids are UUID v4, so the 403/404 split does not expose a
         *     guessable namespace.
         *
         *     Responses carry `X-Content-Type-Options: nosniff` and
         *     `Content-Security-Policy: default-src 'none'; sandbox` so a stored graphic can never
         *     execute as a document, and an `ETag` equal to the stored `content_sha256`.
         *
         *     Because the request needs a bearer token, a browser cannot use a bare
         *     `<img src>`: a client must fetch the bytes with its token and render them from an
         *     object URL it created itself (B1 does this in
         *     `src/features/questions/useQuestionAsset.ts`).
         */
        get: operations["getQuestionAsset"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/{question_id}/statistics": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * De-identified answer statistics (RQ-B-3-30)
         * @description Built exclusively from `autograder.v1.evaluation.completed` events. The
         *     aggregation never reads student source code or student identity. Duplicate
         *     deliveries are deduplicated by evaluation_run_id, so replays are idempotent.
         */
        get: operations["getQuestionStatistics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/{question_id}/authoring-assistance": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Ask for authoring suggestions on a draft question (RQ-T-10)
         * @description Teacher/assistant only, inside the authoring flow. The model reviews the question the
         *     teacher is **currently writing** and returns advice: quality observations, comparison
         *     policy fitness, ambiguous wording, difficulty estimate.
         *
         *     Three boundaries, all from `ADR-0009` decision 3, and all normative:
         *
         *     1. **Advice, never an edit.** Nothing here writes to the question bank. The response is
         *        suggestions; the teacher decides what to change and remains the author of record.
         *        There is deliberately no "apply" parameter -- accepting a suggestion means the
         *        teacher editing the draft through `updateQuestionDraft`, so the audit trail shows a
         *        human action.
         *     2. **Suggestions stay away from student-visible surfaces.** The teacher may show the
         *        model the draft they are writing, but the *response* must not restate reference
         *        answers, hidden cases or expected outputs in any position a student could read. The
         *        response schema has no field for protected content, which is what makes that
         *        structural rather than a promise.
         *     3. **Off by default.** With no provider configured this returns `409` naming the missing
         *        configuration; the authoring flow is fully usable without it.
         */
        post: operations["requestAuthoringAssistance"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-draft-generations/capabilities": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Provider availability, configured input limits and the caller's own budget
         * @description Added with `AI-FLOW-01A`. The generation form has to state the limits it will be held
         *     to -- how long an objective may be, how many cases may be asked for, how long a
         *     candidate survives -- and those are **deployment** decisions (provider context
         *     window, cost per token, retention policy), not contract constants. A client that
         *     printed a hard-coded "1000 characters" would be wrong on every deployment that
         *     configured something else, so the numbers are read from the server that enforces
         *     them.
         *
         *     Nothing here is a secret: no provider URL, no credential, and **no other account's
         *     usage**. `requests_remaining` is `null` when this deployment does not cap requests --
         *     an absent budget must not be rendered as "0 left".
         *
         *     Declared before `/question-draft-generations/{generation_id}` on purpose: a literal
         *     segment under a collection that also has a parameterised child is otherwise parsed as
         *     an id (the trap `questions/coverage` and `quality-summary` both document).
         */
        get: operations["getQuestionDraftGenerationCapabilities"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-draft-generations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Turn a teaching objective into an editable question draft (AI-FLOW-01A)
         * @description `RQ-T-10`. The teacher describes what students should learn; the model returns a
         *     **structured, editable candidate**. This is not the same operation as
         *     `requestAuthoringAssistance`: that one advises on a draft that already exists and
         *     returns prose, this one produces candidate fields and returns a teacher-only
         *     proposal. They are deliberately two endpoints, because mixing an authoritative
         *     review with a guess into one response makes the two indistinguishable.
         *
         *     Three properties, all normative:
         *
         *     1. **A candidate, not a question.** No `question`, `question_version` or `test_case`
         *        row is created here. Nothing is assignable, nothing is publishable, and a model
         *        answer can never become a question without a human going through
         *        `createQuestion` (which validates every field again, including the reference
         *        answer, the case set and the adapter manifest).
         *     2. **Teacher-only content.** The response may contain a proposed reference answer and
         *        proposed hidden cases, so it is not a public DTO and is not shaped like one. The
         *        same data is readable back through `getQuestionDraftGeneration` only by the account
         *        that generated it, while it still holds course write scope; a student calling
         *        either operation gets `403`.
         *     3. **A real call or an honest refusal.** With no provider configured this answers
         *        `503` and makes no outbound request. A provider that fails, times out or answers
         *        something that is not JSON is `502`; the paid attempt is recorded in the
         *        usage ledger either way. Neither case is ever reported as `ready`.
         *
         *     `status` classifies what came back and is the only place a client reads it:
         *
         *       * `ready` -- the candidate parsed cleanly and is editable;
         *       * `needs_review` -- readable, but at least one field is missing or illegal, and each
         *         such field is named in `warnings[].field` so the client can put the message next
         *         to the control;
         *       * `failed` -- a response arrived but the core structure needed to open an editor is
         *         absent (no stem). The record is kept so the teacher can see *why* after a refresh;
         *         it holds no candidate;
         *       * `unavailable` -- no model provider is configured on this deployment. No outbound
         *         call is made, nothing is charged, and manual authoring stays fully usable.
         *
         *     Neither `ready` nor `needs_review` is a statement that the question is correct, fair
         *     or publishable: that is decided by the deterministic diagnostics and by the
         *     reference-answer verification the publish gate requires.
         *
         *     Refusal order is fixed and is a security property, not an implementation detail:
         *     role and course scope (`401`/`403`) are checked before input (`422`), before provider
         *     state (`503`) and before the budget (`429`), so an unauthorized caller cannot learn
         *     this deployment's model configuration by comparing status codes.
         */
        post: operations["createQuestionDraftGeneration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-draft-generations/{generation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /**
                 * @description Identifier of a candidate, **not** a credential. Copying the id to another account
                 *     produces `403`; it grants nothing.
                 */
                generation_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Restore an AI candidate after a refresh (AI-FLOW-01A)
         * @description Returns the **original** candidate again. It deliberately does not restore the
         *     teacher's unsaved edits: those live in the page, and the honest behaviour after a
         *     refresh is "here is what the model proposed, your changes are gone" rather than a
         *     form that looks like it kept them. A client must therefore warn before navigating
         *     away with unsaved edits, and must not persist the proposal in browser storage -- the
         *     only copy is the server-side record read back through this operation with a valid
         *     session.
         *
         *     `404` covers three indistinguishable states: unknown id, discarded candidate, and
         *     expired record. They are collapsed on purpose -- after the retention window nothing
         *     about the content should remain recoverable, and a distinct `410` would confirm that
         *     an id once existed.
         */
        get: operations["getQuestionDraftGeneration"];
        put?: never;
        post?: never;
        /**
         * Discard an unaccepted AI candidate (AI-FLOW-01A)
         * @description The **only** operation that removes an unaccepted proposal. Collapsing the preview
         *     panel must not do this, which is why it is an explicit action behind a confirmation:
         *     a discarded candidate cannot be recovered, while a collapsed one can be reopened.
         *
         *     What is removed is the candidate content (the proposal). What is kept is the
         *     de-identified ledger row -- actor, course, provider/model/prompt version, timing,
         *     token usage, status -- because "a paid call happened and was discarded" is an audit
         *     fact, not content. The `usage` row therefore never held the candidate in the first
         *     place and does not change here.
         *
         *     A candidate that was already saved as a question version cannot be discarded (`409`):
         *     the version exists, and removing the generation record would break the provenance
         *     link from that version back to the call that produced it.
         */
        delete: operations["discardQuestionDraftGeneration"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}/learning-context": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Public question context for B2's student learning hints (AI-FLOW-01B)
         * @description INTERNAL, service identity only. B2 builds a hint from a student's own completed
         *     evaluation, and to explain *what the exercise teaches* it needs the public half of the
         *     question version: title, stem, language, tags and adapter kind. That is the whole of
         *     this response.
         *
         *     **What is deliberately absent, and why it is a separate schema.** No reference answer,
         *     no test case, no expected output, no fixture, no teacher note, no student data. This
         *     is not `QuestionVersionPublic` minus some fields: it is enumerated field by field in
         *     both the contract and the DTO, so a field added to the version DTO later cannot be
         *     sent to a model by accident. A hint generator that knew the expected output could be
         *     persuaded to repeat it into text a student reads, which would turn the hint feature
         *     into the leak.
         *
         *     The caller must be a **service** (`serviceToken`), not a student browser: a browser
         *     endpoint for this would be an enumerable read of question context, and the learning
         *     hints are generated server-to-server. A student calling it gets `403`; a request with
         *     no credential gets `401`, and both are answered before the version is looked up.
         *
         *     Only a version that has been published (or was published and later archived) is
         *     readable. A draft is `409`: an evaluation run can only reference a published version,
         *     so "hints for an unpublished version" is not a state the consumer can legitimately be
         *     in, and serving it would create a read path for unpublished content that no
         *     requirement asks for.
         */
        get: operations["getQuestionVersionLearningContext"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/questions/{question_id}/diagnostics": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Deterministic comparison-policy findings (CHG-0013)
         * @description Reports whether this question's declared comparison policy is likely to misjudge, using
         *     fixed rules over the version's own declarations. Every rule exists because the project
         *     actually hit the failure it describes: `CMP-001` CRLF-vs-LF expected output (the bank
         *     authoring round lost time to it on Windows), `CMP-002` non-ASCII stdin decoding (the
         *     verifier's GBK-vs-C.UTF-8 mismatch, which could make a problem pass locally and behave
         *     differently on the runner), `CMP-003` `exact` combined with trailing whitespace the stem
         *     never promised, `CMP-004` `numeric_tolerance` with no tolerance value.
         *
         *     The response carries the rule id, a severity and readable text. It never echoes an
         *     expected output, which is why it is safe to show a teacher next to a protected version.
         */
        get: operations["getQuestionDiagnostics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}/reference-answer": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * The version's own reference answer, for the verification path (CHG-0023)
         * @description `CHG-0019` requires B2 to execute a version's reference answer so that `publish` can be
         *     backed by a real verification instead of a teacher's self-attestation. That endpoint had
         *     **no implementable semantics** until this one existed: nothing in the contract carried the
         *     reference answer to B2. `CHG-0023` records that finding and closes it.
         *
         *     **Why its own endpoint instead of adding a field to the evaluation package.** The reference
         *     answer is needed only by the *verification* path; the evaluation package is fetched for
         *     *every* evaluation. Putting it in the package would ship a reference solution over the wire
         *     on every evaluation that does not need it -- a needless expansion of protected content in
         *     flight -- and would change the compiler's output, which changes `package_digest` for every
         *     published version and so invalidates every digest already bound into a recorded result.
         *     Neither cost is acceptable for one extra field.
         *
         *     **This is protected content.** It is readable only with `serviceToken` (the same scheme the
         *     evaluation package uses, so no new credential and no new trust relationship), and it is
         *     deliberately **not** the version's public representation: `QuestionVersionPublic` has no
         *     such field and `QuestionDraftInput` documents that the answer is never echoed back through a
         *     public DTO. A teacher reaches the reference answer through the authoring write path, which
         *     is a different, already-existing route.
         *
         *     The response carries **only** the answer. Hidden-case expectations and fixtures are not
         *     copied here: they already have their own channels, and this endpoint exists to add the one
         *     missing input rather than to become a general protected-content firehose.
         *
         *     `409` means **the version's state makes verification impossible**, and it covers exactly two
         *     states (`CHG-0026`). They are named rather than left to be inferred, because the earlier
         *     wording defined `409` as "no reference answer" in one sentence and as "its state makes
         *     verification impossible" in the next -- two readings in one paragraph, and a reader had to
         *     guess which one was the rule:
         *
         *     1. the version has **no reference answer for its declared language**; or
         *     2. the version **has** an answer but no `content_digest` (a version with no test cases has
         *        nothing to attest), so an answer executed against it could not be bound to anything.
         *
         *     The `detail` must say **which** of the two it is: they call for different actions (write the
         *     answer, or add test cases), and a client that collapsed them would send the teacher to fix
         *     the wrong thing.
         *
         *     Deliberately not `404` in either case: the resource is there, and collapsing them would tell
         *     a client "this version does not exist" when it does (`governance.md` hard rule 1).
         */
        get: operations["getReferenceAnswer"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /** Read one immutable question version (public DTO) */
        get: operations["getQuestionVersion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Publish and freeze a question version (RQ-B-3-20)
         * @description Publication validates completeness: a non-empty stem with reachable assets, a
         *     reference answer, at least one test case, a valid adapter declaration and an
         *     explicit comparison policy. On success the version becomes immutable, a
         *     content digest is bound, and `autograder.v1.question.version.published` is
         *     emitted without any protected content.
         *
         *     Added by CHG-0008: publication **also** requires a reference-answer attestation
         *     whose `content_digest` matches the version's recomputed content. Rationale: B3 must
         *     not execute student code and must not execute reference answers either, so it cannot
         *     verify a question itself -- but "the reference answer actually produces the expected
         *     output" is exactly the property that decides whether a student can pass. Without an
         *     attestation requirement, a question whose reference answer never ran can be
         *     published and reach a student. MAIN's bank verifier does run the reference answers
         *     and now records that fact as a digest-bound attestation (`RQ-T-10` context, see
         *     `MAIN/docs/adr/ADR-0009-ai-assistance-two-track.md` and the bank tooling under
         *     `MAIN/scripts/`). Editing the content changes the digest and therefore invalidates
         *     the attestation, so a stale attestation cannot be reused.
         */
        post: operations["publishQuestionVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}/fixtures": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Upload fixture bytes for a draft version (RQ-B-3-50)
         * @description `QuestionDraftInput.adapter.files[]` carries only `{path, content_sha256, mount}`,
         *     so it has nowhere to put bytes; this operation is where they enter. The returned
         *     `content_sha256` is computed by the server over the decoded bytes and is the
         *     authoritative value the manifest must then declare.
         *
         *     **Authorization is the teacher's own bearer token**, within the draft's course write
         *     scope -- not `serviceToken`. Before this change the operation was declared
         *     `internal` + service credential, which made a complete file question impossible from
         *     a browser: the browser does not have (and must never be given) the service token, so
         *     a teacher could pick "file I/O" and then never supply the fixture the adapter needs.
         *     The operation is scoped exactly like every other draft write (course write scope on
         *     the version's question), the digest authority stays in B3, and no credential leaves
         *     the server. Reads are still impossible: fixture bytes have no read endpoint at all.
         *
         *     Only a **draft** version accepts an upload: a published version's content is frozen,
         *     and its package digest already binds the fixture digests it was published with. The
         *     version must exist first, which is why the authoring flow saves the draft and then
         *     uploads -- "draft saved, attachments not finished" is a real state the client must
         *     report as such rather than as a successful save of the files.
         */
        post: operations["uploadVersionFixture"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}/archive": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Archive a published version so it can no longer be assigned
         * @description A version referenced by historical evaluations cannot be deleted; it can only be archived.
         */
        post: operations["archiveQuestionVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}/attestations": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Record a digest-bound reference-answer attestation (CHG-0008)
         * @description Records the fact that some **external verifier** ran this version's reference answer
         *     against every test case and observed the declared expected output. B3 stores and
         *     enforces it; B3 does **not** run anything (`B3/AGENTS.md`: 我禁止：执行学生代码 --
         *     and the same restraint applies to reference answers).
         *
         *     Why the split matters: "the reference answer really produces the expected output"
         *     is the single property that decides whether a question is answerable at all, and
         *     nothing in the platform checked it before a student saw the question. The verifier
         *     executes; B3 only records. That keeps the executor and the recorder separate, which
         *     is the same principle as `CHG-B3-0002` ("a client-declared digest is never trusted"):
         *     here the declared digest is only ever *cross-checked* against the content B3 hashes
         *     itself, so an attestation for different content is rejected rather than believed.
         *
         *     `publish` requires a matching, unexpired attestation. Editing the content changes
         *     the digest and silently invalidates it.
         *
         *     **Only a draft accepts an attestation** (`CHG-0028`). A published version's content is
         *     already frozen, so a new attestation could not change what students receive -- the gate has
         *     to be satisfied *before* the version goes out, not after. This is the other half of why
         *     `getEvaluationPackage` must serve drafts: together they are what makes "verify the draft,
         *     record the attestation, then publish" executable at all. `409` on this operation therefore
         *     means "not a draft".
         */
        post: operations["recordReferenceAttestation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/question-versions/{question_version_id}/evaluation-package": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * Internal evaluation package (service-to-service only)
         * @description INTERNAL. Consumed by B2 only. This is the highest-risk coupling point in the
         *     system: B2's runner core depends on this structure, so it is frozen in
         *     MAIN/contracts/schemas/evaluation-package.schema.json and changes must go
         *     through MAIN/docs/contracts/change-protocol.md.
         *
         *     **Served for a draft as well as a published version** (`CHG-0028`). That is not a
         *     convenience: the publish gate requires a reference-verification attestation before a version
         *     goes out, and `recordReferenceAttestation` accepts only a draft, so the verifier must be
         *     able to obtain the executable inputs **while the version is still a draft**. Refusing drafts
         *     here made the whole publish path impossible -- the teacher could write a draft and then
         *     never publish it.
         *
         *     The two cases differ in one respect, and it is the reason the summary no longer says
         *     "immutable" outright:
         *
         *     * **published** -- an immutable artifact. `created_at` is the version's `published_at`, and
         *       the compiled digest is compared against the digest recorded at publish time. A mismatch
         *       means protected content changed after publication and is refused.
         *     * **draft** -- a snapshot of the version's *current* content, so it changes when the draft
         *       changes. `created_at` is the content's own timestamp (**not** `published_at`, which does
         *       not exist yet), and there is **no** recorded digest to compare against. The response still
         *       carries a `package_digest`: it is the digest of what was just compiled, it is honest, and
         *       it is simply not yet bound to any record.
         *
         *     The response carries hidden test cases and their expected outputs. It does **not** carry the
         *     reference answer -- that has its own channel (`CHG-0023`), added because the earlier
         *     description here claimed otherwise and an implementation was written against that claim.
         *     It must never be reachable from a student-facing surface, and it still requires service
         *     authentication: being internal is not a substitute for authorization.
         *
         *     `409` on this operation means the version has **no compilable content** (for instance, no
         *     test cases at all). It deliberately does **not** mean "this is a draft" -- that reading is
         *     what made the publish path impossible, and `CHG-0028` removed it.
         */
        get: operations["getEvaluationPackage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/assignments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List assignments (作业集合) in the caller's course scope */
        get: operations["listAssignments"];
        put?: never;
        /**
         * Create an assignment from published question versions
         * @description Only published versions may be referenced; a draft reference returns 409.
         */
        post: operations["createAssignment"];
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
         * @description Fixture bytes plus the logical path and mount point they belong to. The digest is
         *     computed by the server; declaring a digest here would let a caller bind a version
         *     to bytes it never uploaded.
         */
        FixtureUploadInput: {
            path: string;
            /**
             * @description `input` is a fixture handed to the code; `expected_output` is a file the code must produce.
             * @default input
             * @enum {string}
             */
            mount: "input" | "expected_output";
            content_base64: string;
        };
        /** @description Stored fixture metadata. The bytes themselves are never returned. */
        FixtureUploadResult: {
            fixture_id: components["schemas"]["Uuid"];
            question_version_id: components["schemas"]["Uuid"];
            path: string;
            /** @enum {string} */
            mount: "input" | "expected_output";
            content_sha256: string;
            size_bytes: number;
        };
        /**
         * @description A stem graphic, base64-encoded. `content_sha256` is computed by the server from
         *     the decoded bytes and is never taken from the request: a client-declared digest
         *     would let a caller bind a version to a graphic it did not upload.
         */
        AssetUploadInput: {
            content_base64: string;
            /** @description Restricted to the image allow-list (png, jpeg, gif, webp, svg+xml). */
            content_type: string;
            alt_text?: string | null;
        };
        /** @description Metadata for a stored graphic. The bytes are read back from the asset path. */
        StemAssetUploadResult: {
            asset_id: components["schemas"]["Uuid"];
            content_type: string;
            content_sha256: string;
            size_bytes: number;
            alt_text: string | null;
        };
        /**
         * @description What to ask about. Kept narrow on purpose: the model is given the question the teacher
         *     is writing and a list of the advice kinds wanted, not free rein over bank state.
         */
        AuthoringAssistanceRequest: {
            /**
             * @description Which kinds of advice are wanted. An enum rather than free text so the prompt
             *     surface stays enumerable -- a caller cannot ask the model to "do anything else",
             *     and every supported use is a documented one.
             */
            topics: ("quality" | "comparison_policy" | "wording" | "difficulty" | "coverage")[];
            /** @description Optional context from the teacher. */
            notes?: string | null;
        };
        /**
         * @description Advice about a draft question (RQ-T-10). Note what is absent: there is no field
         *     carrying a reference answer, a hidden case or an expected output, and no field that
         *     mutates the question. Both omissions are the design (`ADR-0009` decision 3).
         */
        AuthoringAssistance: {
            question_id: components["schemas"]["Uuid"];
            /**
             * @description `unavailable` = no model provider is configured (the default).
             * @enum {string}
             */
            status: "unavailable" | "ready" | "failed";
            /** @description Empty unless `status` is `ready`. */
            suggestions: {
                /** @enum {string} */
                topic: "quality" | "comparison_policy" | "wording" | "difficulty" | "coverage";
                message: string;
                /** @enum {string} */
                confidence?: "advisory";
            }[];
            provenance?: components["schemas"]["AuthoringProvenance"] | null;
            /** @description Non-empty. Clients must show these with the suggestions. */
            disclaimers: string[];
        };
        AuthoringProvenance: {
            provider: string;
            model: string;
            prompt_version: string;
        };
        /**
         * @description What the teacher asks for (AI-FLOW-01A). An **objective plus constraints**, never
         *     question content: no reference answer, no hidden case, no student data and no existing
         *     question travels here, which is why the model cannot be asked to "improve the answer
         *     it was shown".
         *
         *     `language` and `adapter_kind` are teacher decisions, not model output. They are the
         *     constraints the finished exercise has to satisfy, so the candidate echoes them
         *     unchanged: a model that decided to answer in another language or for another adapter
         *     would produce a question the rest of the platform cannot run.
         *
         *     `learning_objective` and `constraints` declare **no maximum length here** on purpose.
         *     The accepted size is a deployment decision -- provider context window, cost per
         *     token, abuse policy -- so freezing one environment's number into the contract would
         *     make every other deployment either wrong or silently truncating. The configured limit
         *     is returned by `getQuestionDraftGenerationCapabilities` and enforced by the server
         *     with a field-level `422`; input is never truncated to fit.
         */
        QuestionDraftGenerationRequest: {
            /** @description What students should learn. Passed to the model as data, never concatenated into the system instruction. */
            learning_objective: string;
            language: components["schemas"]["Language"];
            /**
             * @description Which of the three test frameworks the candidate must be runnable as (RQ-B-3-40/50/60).
             * @enum {string}
             */
            adapter_kind: "stdin" | "file" | "callable";
            difficulty?: string | null;
            /** @description Course tags the candidate should align with. Guidance, not a closed vocabulary. */
            tags?: string[];
            desired_case_count?: number | null;
            constraints?: string | null;
        };
        /**
         * @description One AI candidate and everything a teacher needs to judge it (AI-FLOW-01A).
         *
         *     **Teacher-only.** `proposal` may carry a reference answer and hidden cases, so this
         *     schema -- like `ReferenceAnswer` -- is a protected surface: it is returned only to the
         *     account that generated the candidate while it holds course write scope, and it is not
         *     shaped like `QuestionPublic`/`QuestionVersionPublic` because a student-facing DTO must
         *     have no field that could hold any of it.
         *
         *     The candidate never becomes a question by itself. Saving is an ordinary
         *     `createQuestion` call with a human-edited body (plus this `generation_id`), and every
         *     field is validated again there -- the candidate's own validation exists to place
         *     messages next to controls, not to guarantee a publishable question.
         *
         *     `status` is the only place the outcome is read, for every non-error case:
         *
         *       * `ready` -- parsed cleanly and editable;
         *       * `needs_review` -- readable, with field-level problems named in `warnings`;
         *       * `failed` -- no editor could be formed (for instance no stem); `proposal` is null;
         *       * `unavailable` -- this deployment has no model provider; no call was made.
         *
         *     `ready` and `needs_review` both mean "human review required"; neither means correct,
         *     fair or publishable.
         */
        QuestionDraftGeneration: {
            /** @description Identifier used to restore the candidate after a refresh. Null only when no candidate was stored (`unavailable`). */
            generation_id?: components["schemas"]["Uuid"] | null;
            course_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            status: "ready" | "needs_review" | "failed" | "unavailable";
            /** @description The editable candidate. Null for `failed` and `unavailable`, and after the candidate was accepted or discarded. */
            proposal?: components["schemas"]["QuestionDraftProposal"] | null;
            /** @description Field-level problems, each naming the control it belongs to. Empty only for a clean `ready`. */
            warnings: components["schemas"]["DraftGenerationWarning"][];
            /** @description Where the candidate came from. Null for `unavailable`: there is no model to attribute a non-answer to. */
            provenance?: components["schemas"]["AuthoringProvenance"] | null;
            /** @description Non-empty. Must be rendered with the candidate: it is a draft, not an answer key. */
            disclaimers: string[];
            created_at?: components["schemas"]["Timestamp"] | null;
            /** @description When the server will refuse to serve this candidate. The retention window is deployment-configured. */
            expires_at?: components["schemas"]["Timestamp"] | null;
            /** @description The version this candidate became, once a teacher saved it. Null while the candidate is unaccepted. */
            accepted_question_version_id?: components["schemas"]["Uuid"] | null;
        };
        /**
         * @description PROTECTED candidate fields. `reference_answer` and every `expected_*` below are the
         *     content the question bank keeps out of every public DTO, and they appear here only
         *     because this is the teacher's own drafting surface.
         *
         *     Every field is optional except the shape of the exercise itself. Missing optional
         *     content is a warning, not a failure: the teacher fills it in. Nothing is invented to
         *     fill a gap -- no title, no stem, no answer, no expected output.
         */
        QuestionDraftProposal: {
            /** @description May be empty when the model proposed none: a title is the teacher's decision. */
            title: string;
            stem_markdown: string;
            language: components["schemas"]["Language"];
            /** @enum {string} */
            adapter_kind: "stdin" | "file" | "callable";
            tags: string[];
            /** @description PROTECTED. A proposed solution the teacher still has to verify through the publish gate. */
            reference_answer?: string | null;
            cases?: components["schemas"]["QuestionDraftProposalCase"][];
            /** @description Text fixtures only. A digest is never proposed: B3 computes it after the teacher uploads the bytes. */
            fixtures?: components["schemas"]["QuestionDraftProposalFixture"][];
            adapter?: components["schemas"]["QuestionDraftProposalAdapter"];
            examples?: {
                input: string;
                output: string;
                explanation?: string | null;
            }[];
            knowledge_points?: string[];
            difficulty?: string | null;
            /** @description Why the candidate is shaped this way. Read-only prose for the teacher. */
            notes?: string | null;
        };
        /**
         * @description PROTECTED. One proposed test case.
         *
         *     `weight` is nullable because an unusable number must not be carried forward: a
         *     negative, `NaN` or infinite weight is replaced by `null` plus a field-level warning, so
         *     the editor shows an empty box the teacher has to fill rather than a value that would
         *     poison a score. The ordinal follows the row order in the editor, so reordering a case
         *     renumbers it instead of leaving two cases claiming the same slot.
         */
        QuestionDraftProposalCase: {
            /** @description May be empty when the model proposed none; the teacher names it. */
            name: string;
            ordinal: number;
            weight?: number | null;
            /** @default false */
            visible_to_student: boolean;
            stdin?: string | null;
            args?: string[];
            /** @description PROTECTED. */
            expected_stdout?: string | null;
            /** @description PROTECTED. */
            expected_stderr?: string | null;
            expected_exit_code?: number | null;
        };
        /**
         * @description A **text** fixture the teacher still has to upload. Content only: there is no digest
         *     field, because a digest is what binds bytes to a version and B3 computes it from the
         *     uploaded bytes. A candidate that could declare a digest would be a client-declared
         *     digest in disguise.
         */
        QuestionDraftProposalFixture: {
            path: string;
            /** @enum {string} */
            mount: "input" | "expected_output";
            content_text: string;
        };
        /**
         * @description Suggested manifest pieces. Deliberately **not** a complete `AdapterManifest`: at
         *     generation time a file question has no uploaded fixture yet, so claiming a complete
         *     manifest would be a claim the server has not checked. Each piece here is validated on
         *     its own (argument vector, exit code, comparison modes, import target), and the whole
         *     manifest is validated again when the teacher saves.
         */
        QuestionDraftProposalAdapter: {
            args?: string[];
            stdin?: string | null;
            expect_exit_code?: number | null;
            comparison?: {
                /** @enum {string} */
                stdout?: "exact" | "ignore_trailing_whitespace" | "numeric_tolerance" | "ignored";
                /** @enum {string} */
                stderr?: "exact" | "ignore_trailing_whitespace" | "ignored";
                numeric_tolerance?: number | null;
            } | null;
            callable?: {
                module?: string | null;
                attribute?: string | null;
                arguments_json?: string | null;
            } | null;
        };
        /**
         * @description One field-level issue with a candidate. `field` names the form control, which is what
         *     turns "the model's answer was imperfect" into "this box is wrong". The message
         *     describes the problem and never quotes protected content.
         */
        DraftGenerationWarning: {
            /** @description Dotted path into the candidate, e.g. `cases[2].weight`. */
            field: string;
            message: string;
            /** @enum {string} */
            severity: "error" | "warning" | "info";
        };
        /**
         * @description What the generation form may state as fact (AI-FLOW-01A). Read from the deployment's
         *     configuration, so the control shows the number the server actually enforces. No
         *     credential, no provider URL, no other account's usage.
         */
        QuestionDraftGenerationCapabilities: {
            course_id: components["schemas"]["Uuid"];
            /**
             * @description `unavailable` is a configuration state, not a failure.
             * @enum {string}
             */
            provider_status: "enabled" | "unavailable";
            provider?: string | null;
            model?: string | null;
            learning_objective_max_chars: number;
            constraints_max_chars: number;
            desired_case_count_max: number;
            proposal_max_cases: number;
            retention_seconds: number;
            /** @description Null when this deployment does not cap requests. Must not be rendered as 0 remaining. */
            requests_remaining?: number | null;
            retry_after?: components["schemas"]["Timestamp"] | null;
            disclaimers: string[];
        };
        /**
         * @description The public half of one published version, for B2's student learning hints
         *     (AI-FLOW-01B). Five fields, enumerated rather than filtered from
         *     `QuestionVersionPublic`: no reference answer, no case, no expected output, no fixture,
         *     no teacher note, no student data.
         *
         *     The consumer is B2 acting as a service: a hint is generated from a student's own
         *     completed run, and this is the only question context that generation is allowed to
         *     see. A separate schema is what makes "the hint generator cannot be handed the answer"
         *     a property of the contract instead of a habit.
         */
        QuestionLearningContext: {
            question_version_id: components["schemas"]["Uuid"];
            title: string;
            stem_markdown: string;
            language: components["schemas"]["Language"];
            tags: string[];
            /** @enum {string} */
            adapter_kind: "stdin" | "file" | "callable";
        };
        /**
         * @description Structural delta between two consecutive question versions (CHG-0013). Every field is
         *     a count, a digest or a presence flag, so nothing here can reveal protected content;
         *     B3's own boundary test rejects a protected key name, and the key set is asserted.
         */
        RevisionDiff: {
            case_count_before?: number | null;
            case_count_after?: number | null;
            case_set_changed?: boolean | null;
            weights_changed?: boolean | null;
            fixtures_changed?: boolean | null;
            stem_length_before?: number | null;
            stem_length_after?: number | null;
            adapter_kind_changed?: boolean | null;
            size_limits_changed?: boolean | null;
            /** @description Digest only -- the answer itself never leaves the protected store. */
            reference_answer_digest_before?: components["schemas"]["Sha256"] | null;
            reference_answer_digest_after?: components["schemas"]["Sha256"] | null;
        };
        /**
         * @description Deterministic comparison-policy findings for one question (CHG-0013). These are **rules,
         *     not model advice**: the same input always yields the same list, so a teacher may treat
         *     them as part of the question's quality record. Model-generated authoring suggestions
         *     live on a different endpoint (`RQ-T-10`) and carry `confidence: advisory`; mixing the
         *     two would make an authoritative finding indistinguishable from a guess.
         */
        ComparisonDiagnostics: {
            question_id: components["schemas"]["Uuid"];
            findings: {
                rule_id: string;
                /** @enum {string} */
                severity: "error" | "warning" | "info";
                message: string;
                /** @description Which case the finding is about, when it is case-specific. */
                ordinal?: number | null;
            }[];
        };
        /**
         * @description How a course's question bank is distributed across tags (RQ-B-3-20, CHG-0020). Every field
         *     is a count or a tag string; there is deliberately no `question_id` list, because a
         *     distribution with ids attached is a way to walk the bank rather than a way to see its shape.
         *
         *     One thing this shape must **not** be read as: `sum(tags[].question_count)` is not
         *     `total_questions`. A question may carry several tags (`QuestionDraftInput.tags` allows up to
         *     20), so per-tag counts overlap by design. The checkable identity is
         *     `tagged_questions + untagged_questions == total_questions`, and it is what a consumer should
         *     assert; the per-tag numbers answer "how many questions mention this topic", which is a
         *     different and equally useful question.
         */
        QuestionTagCoverage: {
            course_id: components["schemas"]["Uuid"];
            /** @description Questions visible to this caller in this scope -- the same visibility rule `listQuestions` applies. */
            total_questions: number;
            /** @description Questions carrying at least one tag. */
            tagged_questions: number;
            /** @description Questions with no tag at all. Reported separately so an untagged bank reads as *untagged* rather than as an empty response (the same discipline as `unmeasured_runs` in `CHG-0015`). */
            untagged_questions: number;
            /** @description Per-tag counts, highest first; ties broken by tag ascending so the response is deterministic rather than merely sorted. */
            tags: {
                tag: string;
                /** @description Questions carrying this tag. Never zero: a tag with no questions is absent from the list rather than present with a 0. */
                question_count: number;
            }[];
        };
        /**
         * @description PUBLIC representation. Reference answers, hidden test cases and expected
         *     outputs are intentionally absent from this schema; a contract test asserts
         *     their absence in real responses. Adding any of them here would be a P3
         *     contract violation.
         */
        QuestionPublic: {
            question_id: components["schemas"]["Uuid"];
            course_id: components["schemas"]["Uuid"];
            title: string;
            /** @description Rich-text stem. Rendered as a safe subset; embedded scripts and inline handlers are rejected by the client. */
            stem_markdown: string;
            /** @description Controlled asset references for graphics in the stem (RQ-B-1-40). */
            stem_assets?: {
                asset_id: components["schemas"]["Uuid"];
                content_type: string;
                alt_text?: string | null;
            }[];
            language: components["schemas"]["Language"];
            /** @enum {string} */
            status: "draft" | "published" | "archived";
            /**
             * @description Topic tags in their declared form. `CHG-0022` added the shape here: tags are
             *     validated on write and **rejected, never rewritten** (`domain/tags.py`), and re-read
             *     values are validated again, so these bounds and pattern are facts about what this
             *     system can emit rather than a preference. Before `CHG-0022` this field said only
             *     `type: string`, which left a consumer unable to tell whether a tag could be reused
             *     as a `listQuestions.tags` filter value.
             */
            tags?: string[];
            /**
             * @description Full version history, newest first. Required, not decorative: `publish`
             *     takes a `question_version_id`, and without a way to discover one the publish
             *     flow is unreachable by any API client. It is also what RQ-B-1-40 acceptance
             *     5 needs to render a historical version.
             */
            versions?: components["schemas"]["QuestionVersionSummary"][];
            created_at?: components["schemas"]["Timestamp"];
            updated_at?: components["schemas"]["Timestamp"];
        };
        /** @description One entry of a question's version history. */
        QuestionVersionSummary: {
            question_version_id: components["schemas"]["Uuid"];
            version: number;
            /** @enum {string} */
            status: "draft" | "published" | "archived";
            /**
             * @description When this version was published. **Null for a draft** (`CHG-0024`): a draft has no
             *     publication time, and omitting the field instead would make "absent" mean both "not
             *     published yet" and "this response predates the field". Before `CHG-0024` this was
             *     declared non-nullable, so any version history containing a draft -- the normal state
             *     during authoring -- could not be read by a strict consumer of this contract.
             */
            published_at?: components["schemas"]["Timestamp"] | null;
        };
        /** @description PUBLIC representation of an immutable question version. */
        QuestionVersionPublic: {
            question_version_id: components["schemas"]["Uuid"];
            question_id: components["schemas"]["Uuid"];
            version: number;
            /** @enum {string} */
            status: "draft" | "published" | "archived";
            language: components["schemas"]["Language"];
            /**
             * @description Which of the three test frameworks the version uses (RQ-B-3-40/50/60).
             * @enum {string}
             */
            adapter_kind: "stdin" | "file" | "callable";
            /** @description Bound into every evaluation, so a change to the question cannot silently alter past results. */
            content_digest?: components["schemas"]["Sha256"];
            /**
             * @description Why this version exists (CHG-0013). Optional, and only meaningful for versions
             *     after the first: an immutable-version model is only auditable if the *reason* for a
             *     new version is recorded next to it, otherwise a reader sees two versions and has to
             *     guess what changed. B3 derives a structural delta automatically, but a derived
             *     delta cannot say "the deadline example was wrong".
             */
            revision_note?: string | null;
            /**
             * @description Structural difference from the previous version (CHG-0013): counts, digests and
             *     presence flags only. It deliberately carries **no** protected content -- no
             *     reference answer, no hidden case, no expected output -- so it is safe on the public
             *     version DTO. Null for a first version, where there is nothing to diff against.
             */
            revision_diff?: components["schemas"]["RevisionDiff"] | null;
            /**
             * @description The stem **of this version**. A version is an immutable snapshot, so
             *     rendering a historical version needs its own stem; substituting the
             *     question's current stem would show the wrong content while looking correct
             *     (RQ-B-1-40 acceptance 5).
             */
            stem_markdown: string;
            /** @description Graphics attached to this version, same shape as QuestionPublic.stem_assets. */
            stem_assets?: {
                asset_id: components["schemas"]["Uuid"];
                content_type: string;
                alt_text?: string | null;
            }[];
            /**
             * @description When this version was published. **Null for a draft** (`CHG-0024`): a draft has no
             *     publication time, and omitting the field instead would make "absent" mean both "not
             *     published yet" and "this response predates the field". Before `CHG-0024` this was
             *     declared non-nullable, so any version history containing a draft -- the normal state
             *     during authoring -- could not be read by a strict consumer of this contract.
             */
            published_at?: components["schemas"]["Timestamp"] | null;
            max_files?: number;
            max_total_bytes?: number;
        };
        /**
         * @description Editable draft content. `reference_answer` and `cases` are accepted on this
         *     write path only and are never echoed back through a public DTO.
         *
         *     **Omission preserves (CHG-0017).** Both protected fields are optional, and leaving
         *     one out means "I am not saying anything about this field", not "clear it":
         *
         *       * `cases` omitted -- the version keeps the cases it already has. Supplying it
         *         replaces the whole set. There is deliberately no way to clear every case: a
         *         version with no cases cannot be evaluated, so it would be a state the rest of
         *         the system has to treat as broken rather than as empty.
         *       * `reference_answer` omitted **or blank** -- the stored answer is kept as it is.
         *         A blank string is not a way to remove it either, for the same reason.
         *
         *     This is what lets a caller rename a question or correct its tags without reading and
         *     re-submitting hidden test cases and the reference answer. Before CHG-0017 the only
         *     accepted shape required `cases` on every write, so every edit -- including a
         *     title-only one -- had to carry the protected content out to the client and back.
         *     That is precisely the round-trip the executor/recorder split exists to avoid.
         */
        QuestionDraftInput: {
            title: string;
            stem_markdown: string;
            language: components["schemas"]["Language"];
            /**
             * @description PROTECTED. Never leaves the question bank. Omitted or blank keeps the stored
             *     answer unchanged (CHG-0017); omission never removes it.
             */
            reference_answer?: string;
            adapter: components["schemas"]["AdapterManifest"];
            /**
             * @description PROTECTED. Omitted keeps the version's existing cases unchanged; supplied
             *     replaces the whole set (CHG-0017). Never echoed back through a public DTO.
             */
            cases?: components["schemas"]["TestCaseInput"][];
            /** @default 20 */
            max_files: number;
            /** @default 1048576 */
            max_total_bytes: number;
            /**
             * @description Topic tags. Added by CHG-0008. `tags` already existed in B3's public DTO, in the
             *     `b3` schema and in `QuestionPublic` -- but the **write** path
             *     (`QuestionDraftInput`, closed with `additionalProperties: false`) had no such
             *     field, so no client could ever set one: the read path declared a value that was
             *     structurally impossible to produce. Every question in the bank therefore has an
             *     empty tag list. Normalised form is lowercase with hyphens; the vocabulary itself
             *     belongs to B3 and is validated there.
             */
            tags?: string[];
            /**
             * @description Why this revision exists (CHG-0013). Only meaningful together with
             *     `as_new_version=true`: when a published version is superseded, this is where the
             *     author records *why*. Without it a reader sees two immutable versions and has to
             *     guess what changed and for what reason -- and B3's automatically derived structural
             *     delta cannot express "the deadline example was wrong".
             */
            revision_note?: string | null;
        };
        /**
         * @description A version's own reference answer, delivered to the verification path (CHG-0023).
         *
         *     **PROTECTED.** Never returned by any public DTO, never placed in a contract example, never
         *     logged and never published in an event. `b3.reference_answers` sits in the module's
         *     `PROTECTED_TABLES`, and this schema is the only contract surface that names its content.
         *
         *     `content_digest` is the *same* digest `CHG-0008`'s attestation binds to, which is what makes
         *     "the answer that was executed" and "the content that was attested" the same thing rather
         *     than two values that merely happen to agree.
         */
        ReferenceAnswer: {
            question_version_id: components["schemas"]["Uuid"];
            /** @description The version's content digest. The reference answer is part of the content it covers, so this binds answer to version. */
            content_digest: components["schemas"]["Sha256"];
            language: components["schemas"]["Language"];
            /**
             * @description The version's declared framework. Repeats `QuestionVersionPublic`'s vocabulary rather than inventing a second one.
             * @enum {string}
             */
            adapter_kind: "stdin" | "file" | "callable";
            /** @description PROTECTED. The answer's source. Exactly one answer exists per (version, language); this is the one for the version's declared language. */
            reference_answer: string;
        };
        /**
         * @description Declarative description of how a question is exercised. One shared structure
         *     for all three frameworks so that adding a question type does not require
         *     changing B2's runner core (RQ-B-3-40/50/60).
         */
        AdapterManifest: {
            /** @enum {string} */
            kind: "stdin" | "file" | "callable";
            /** @description Argument vector. Passed as an array, never interpolated into a shell string. */
            args?: string[];
            stdin?: string | null;
            expect_exit_code?: number | null;
            /** @description Declared by the question author. Student code must not be able to influence it. */
            comparison?: {
                /** @enum {string} */
                stdout: "exact" | "ignore_trailing_whitespace" | "numeric_tolerance" | "ignored";
                /** @enum {string} */
                stderr?: "exact" | "ignore_trailing_whitespace" | "ignored";
                numeric_tolerance?: number | null;
            };
            /** @description Required for kind=file: fixtures copied into the sandbox workspace. */
            files?: {
                path: string;
                content_sha256: components["schemas"]["Sha256"];
                /** @enum {string} */
                mount?: "input" | "expected_output";
            }[];
            /** @description Required for kind=callable: the entry point resolved inside the workspace. */
            callable?: {
                module: string;
                attribute: string;
                arguments_json?: string | null;
            };
        };
        /** @description PROTECTED. A hidden test case; never present in a public DTO or an event. */
        TestCaseInput: {
            name: string;
            ordinal: number;
            /** @default 1 */
            weight: number;
            /** @default false */
            visible_to_student: boolean;
            stdin?: string | null;
            args?: string[];
            /** @description PROTECTED. */
            expected_stdout?: string | null;
            /** @description PROTECTED. */
            expected_stderr?: string | null;
            expected_exit_code?: number | null;
        };
        /**
         * @description A verifier's claim that a version's reference answer reproduces its expected
         *     outputs (CHG-0008). The execution happens outside B3; this is only the record.
         */
        ReferenceAttestationInput: {
            /**
             * @description Digest of the *content the verifier ran*. B3 recomputes the version's content
             *     digest and rejects a mismatch (409), so an attestation can never be carried over
             *     to different content.
             */
            content_digest: components["schemas"]["Sha256"];
            /** @description Who ran it, e.g. `MAIN/scripts/verify_question_bank.py`. */
            verifier: string;
            cases_total: number;
            cases_passed: number;
            verified_at?: components["schemas"]["Timestamp"] | null;
            notes?: string | null;
        };
        /**
         * @description PUBLIC shape of an attestation. It deliberately carries **no** reference answer, no
         *     hidden case and no expected output -- only the fact, the counts and the digest, all
         *     of which are safe for a teacher to see. A student-visible surface is not defined:
         *     "this question was verified" is not information a student needs.
         */
        ReferenceAttestationPublic: {
            attestation_id: components["schemas"]["Uuid"];
            question_version_id: components["schemas"]["Uuid"];
            content_digest: components["schemas"]["Sha256"];
            verifier: string;
            cases_total: number;
            cases_passed: number;
            verified_at?: components["schemas"]["Timestamp"] | null;
        };
        /**
         * @description A homework set that groups published question versions for one section.
         *     Recorded by CHG-0007: the operation previously returned an undocumented body
         *     ("Page of assignments."), so a client could not tell what an assignment even
         *     contains — while the platform had already created eight of them. The shape below
         *     is what B3 in fact returns and what the live API was measured to return.
         */
        AssignmentPublic: {
            assignment_id: components["schemas"]["Uuid"];
            course_id: components["schemas"]["Uuid"];
            section_id: components["schemas"]["Uuid"];
            title: string;
            /** @enum {string} */
            status: "open" | "archived";
            due_at?: components["schemas"]["Timestamp"] | null;
            question_version_ids: components["schemas"]["Uuid"][];
            created_at?: components["schemas"]["Timestamp"] | null;
            updated_at?: components["schemas"]["Timestamp"] | null;
        };
        /**
         * @description Aggregate statistics derived solely from de-identified
         *     `evaluation.completed` events. Contains no student identity and no source
         *     code. See MAIN/docs/api/events.md section 3.4.
         */
        QuestionStatistics: {
            question_id: components["schemas"]["Uuid"];
            question_version_id?: components["schemas"]["Uuid"] | null;
            /** @description Total evaluation runs (RQ-B-3-30 回答次数分布). */
            attempt_count: number;
            pass_rate: number;
            /** @description Bucket label to count, e.g. {"0-59": 3, "60-79": 8}. */
            score_distribution?: {
                [key: string]: number;
            };
            failure_category_distribution?: {
                [key: string]: number;
            };
            computed_at?: components["schemas"]["Timestamp"];
        };
        /**
         * Format: uuid
         * @description UUID v4 (RFC 9562), generated by the owning module. See MAIN/docs/api/identifiers.md.
         */
        Uuid: string;
        /**
         * @description Graded language. Required by RQ-B-0-10; B2 selects the interpreter from this value.
         * @enum {string}
         */
        Language: "python" | "bash";
        /** @description Cursor pagination envelope. Offset pagination is forbidden for large tables. */
        CursorPage: {
            items: unknown[];
            next_cursor?: string | null;
        };
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
        /**
         * Evaluation package
         * @description Immutable, self-describing evaluation package compiled by B3 from a published question version and consumed by B2's runner core. This is the highest-risk coupling point in the system: B2 must be able to execute a package without understanding the question's semantics, and adding a question type must not require changing the runner core (RQ-B-3-40/50/60). The package carries reference answers, hidden test cases and expected outputs, so it is service-to-service only and must never be reachable from a student-facing surface.
         */
        "evaluation-package.schema": {
            /**
             * Format: uuid
             * @description The immutable question version this package was compiled from.
             */
            question_version_id: string;
            /** @description Canonical digest of the package content. Bound into every evaluation and reported in the result so a score can be traced to the exact package that produced it. */
            package_digest: string;
            /**
             * @description Graded language (RQ-B-0-10). B2 selects interpreter, static checker and sandbox policy from this value; a submission in a different language is rejected with 422.
             * @enum {string}
             */
            language: "python" | "bash";
            /** @description How the submission is exercised. One shared structure for all three frameworks. */
            adapter: {
                /**
                 * @description stdin = command line input (RQ-B-3-40); file = reads test data from files (RQ-B-3-50); callable = imported and invoked (RQ-B-3-60).
                 * @enum {string}
                 */
                kind: "stdin" | "file" | "callable";
                /** @description Argument vector passed as an array. It is never interpolated into a shell string, so arguments containing shell metacharacters stay literal. */
                args?: string[];
                stdin?: string | null;
                expect_exit_code?: number | null;
                /** @description Comparison policy declared by the question author. Student code must not be able to influence it: the runner takes it from the package, never from anything the submission provides. */
                comparison?: {
                    /** @enum {string} */
                    stdout: "exact" | "ignore_trailing_whitespace" | "numeric_tolerance" | "ignored";
                    /** @enum {string} */
                    stderr?: "exact" | "ignore_trailing_whitespace" | "ignored";
                    numeric_tolerance?: number | null;
                };
                /** @description Fixtures copied into the sandbox workspace before execution (relevant for kind=file). Paths are workspace-relative; absolute paths and '..' are rejected. */
                files?: {
                    path: string;
                    content_sha256: string;
                    /** @enum {string} */
                    mount: "input" | "expected_output";
                }[];
                /** @description Entry point resolved inside the workspace (relevant for kind=callable). Loading from arbitrary absolute paths or over the network is forbidden. */
                callable?: {
                    module: string;
                    attribute: string;
                    arguments_json?: string | null;
                };
            };
            /** @description Hidden test cases. Never present in a public DTO, an event, a log line or a student-visible export. */
            tests: {
                /** Format: uuid */
                case_id: string;
                name: string;
                /** @description Stable execution order; results are reported per ordinal so evidence can be compared across runs. */
                ordinal: number;
                /** @default 1 */
                weight: number;
                /**
                 * @description Whether the student may see this case's input and expected output. Defaults to false; hidden cases must stay hidden.
                 * @default false
                 */
                visible_to_student: boolean;
                stdin?: string | null;
                args?: string[];
                expected_stdout?: string | null;
                expected_stderr?: string | null;
                expected_exit_code?: number | null;
            }[];
            /** @description Sandbox policy applied to every case in this package. The constants encode non-negotiable guarantees from ADR-0004: no network, read-only root, non-root user. Numeric limits are enforced by cgroup v2 and seccomp. */
            security_policy: {
                /**
                 * @description Network access is always denied. A package cannot opt out.
                 * @constant
                 */
                network: false;
                /**
                 * @description The root filesystem is always read-only. A package cannot opt out.
                 * @constant
                 */
                read_only_root: true;
                /**
                 * @description Graded code never runs as root. A package cannot opt out.
                 * @constant
                 */
                allow_root: false;
                /** @description Bounded by the resource ledger: a single concurrent sandbox job is capped at 96 MB. */
                max_memory_bytes: number;
                max_cpu_seconds: number;
                max_wall_seconds: number;
                max_output_bytes: number;
                max_processes: number;
                max_files_created?: number | null;
            };
            /** @description Submission file count limit declared per question (RQ-B-2-10). */
            max_files: number;
            /** @description Submission total size limit declared per question. */
            max_total_bytes: number;
            /**
             * Format: date-time
             * @description When this package's content was fixed. For a published version it equals `published_at` (the package is an immutable artifact); for a draft (CHG-0028) it is the draft content's own timestamp, because a draft has no `published_at` and its package is a snapshot of current content rather than a frozen one. Excluded from `package_digest`, so it never affects the digest.
             */
            created_at: string;
        };
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
        /** @description Schema-valid request rejected by a business rule. */
        UnprocessableEntity: {
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
        /** @description State conflict, e.g. an immutable version, a referenced version or a concurrent edit. */
        Conflict: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /** @description Rate limit exceeded. */
        TooManyRequests: {
            headers: {
                [name: string]: unknown;
            };
            content: {
                "application/problem+json": components["schemas"]["ProblemDetails"];
            };
        };
        /**
         * @description An upstream dependency answered, but not with something this service can use.
         *
         *     Distinct from `ServiceUnavailable` on purpose: "there is no dependency" and "the
         *     dependency answered garbage" call for different operator actions, and collapsing them
         *     would make a malformed model response indistinguishable from an unconfigured
         *     deployment. The response `type` names the classification and the `detail` never
         *     contains the upstream body -- an upstream error page can echo the request, which would
         *     put a user's input into an error message.
         */
        BadGateway: {
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
    };
    parameters: {
        Cursor: string;
        Limit: number;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    listQuestions: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                course_id?: components["schemas"]["Uuid"];
                language?: components["schemas"]["Language"];
                published_only?: boolean;
                /**
                 * @description Filter by topic tag (CHG-0013). Added because tags became writable in CHG-0008 and
                 *     were then readable but not *retrievable*: a teacher could label 106 questions and
                 *     still have no way to ask for "the process-scheduling ones". Matching is all-of
                 *     rather than any-of, which is the useful reading for a coverage view; a caller who
                 *     wants a union can repeat the request.
                 */
                tags?: string[];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of public question DTOs. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage"] & {
                        items?: components["schemas"]["QuestionPublic"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
        };
    };
    createQuestion: {
        parameters: {
            query: {
                course_id: components["schemas"]["Uuid"];
                /**
                 * @description The AI-FLOW-01A candidate this draft was edited from, when there is one.
                 *
                 *     It is a query parameter rather than a body field because `QuestionDraftInput` is
                 *     closed (`additionalProperties: false`) and, more importantly, because it is not
                 *     question content: the body is the teacher's own edited draft and is validated
                 *     exactly as any other write. The id only records provenance -- B3 verifies that the
                 *     generation belongs to the same actor and course, that it was neither discarded nor
                 *     already accepted, and then binds it to the version **in the same transaction**,
                 *     removing the candidate content. A draft that carries a generation id from someone
                 *     else's candidate is refused (403), and a candidate is refused when the write it
                 *     would have been associated with rolls back.
                 */
                generation_id?: components["schemas"]["Uuid"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuestionDraftInput"];
            };
        };
        responses: {
            /** @description Question and draft version created. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionPublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getQuestionTagCoverage: {
        parameters: {
            query: {
                course_id: components["schemas"]["Uuid"];
                /** @description Same default and the same reason as `listQuestions`: a client that forgets this parameter must see less, never more. */
                published_only?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Per-tag counts, plus how many questions carry at least one tag. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionTagCoverage"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getQuestion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Public question DTO. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionPublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    updateQuestionDraft: {
        parameters: {
            query?: {
                /**
                 * @description Declared by CHG-0008. B3 has implemented this parameter since the version model
                 *     was built (`B3/src/autograder_b3/api/router.py`), but it was absent from the
                 *     contract, so a client could not discover it and an API client generated from
                 *     this file could not use it at all -- while the summary above promised the
                 *     opposite behaviour for a published version.
                 */
                as_new_version?: boolean;
            };
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuestionDraftInput"];
            };
        };
        responses: {
            /** @description Draft updated. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionPublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    uploadQuestionAsset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssetUploadInput"];
            };
        };
        responses: {
            /** @description Stored asset metadata. The bytes are read back from the asset path. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StemAssetUploadResult"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getQuestionAsset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
                asset_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Image bytes. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "image/png": string;
                    "image/jpeg": string;
                    "image/gif": string;
                    "image/webp": string;
                    "image/svg+xml": string;
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    getQuestionStatistics: {
        parameters: {
            query?: {
                from?: components["schemas"]["Timestamp"];
                to?: components["schemas"]["Timestamp"];
            };
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Aggregate statistics for the question. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionStatistics"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    requestAuthoringAssistance: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AuthoringAssistanceRequest"];
            };
        };
        responses: {
            /** @description Suggestions. The draft is unchanged. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AuthoringAssistance"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getQuestionDraftGenerationCapabilities: {
        parameters: {
            query: {
                course_id: components["schemas"]["Uuid"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Provider state, configured limits and the caller's remaining budget. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionDraftGenerationCapabilities"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    createQuestionDraftGeneration: {
        parameters: {
            query: {
                /** @description The course the draft is being written for. Used for authorization and for the candidate's ownership; never sent to the model. */
                course_id: components["schemas"]["Uuid"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuestionDraftGenerationRequest"];
            };
        };
        responses: {
            /**
             * @description The candidate, its field-level warnings, its provenance and its expiry. Returned
             *     for `ready`, `needs_review`, `failed` and `unavailable` alike -- the status, not the
             *     HTTP code, says what happened, so a client has one shape and one branch to read.
             *
             *     `unavailable` is a **configuration state**, not a failure: no provider is
             *     configured on this deployment, nothing left the process, nothing was charged and
             *     nothing was stored (`proposal`, `generation_id`, `provenance` and `expires_at` are
             *     all `null`). It is reported here rather than as `503` because the editor needs to
             *     say "AI drafts are not enabled here, you can still write this question" next to the
             *     button it disables, and a client that had to distinguish a deployment fact from a
             *     runtime failure by status code would end up showing the wrong one. A **configured**
             *     provider that misbehaves is `502`, which is a different statement with a different
             *     operator action.
             */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionDraftGeneration"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            422: components["responses"]["UnprocessableEntity"];
            429: components["responses"]["TooManyRequests"];
            502: components["responses"]["BadGateway"];
        };
    };
    getQuestionDraftGeneration: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /**
                 * @description Identifier of a candidate, **not** a credential. Copying the id to another account
                 *     produces `403`; it grants nothing.
                 */
                generation_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The stored candidate, unchanged. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionDraftGeneration"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    discardQuestionDraftGeneration: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /**
                 * @description Identifier of a candidate, **not** a credential. Copying the id to another account
                 *     produces `403`; it grants nothing.
                 */
                generation_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Candidate content removed; de-identified ledger row kept. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    getQuestionVersionLearningContext: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The public half of one published version. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionLearningContext"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    getQuestionDiagnostics: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Findings; an empty list means no rule fired, not "not checked". */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ComparisonDiagnostics"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    getReferenceAnswer: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The version's reference answer for its declared language. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReferenceAnswer"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    getQuestionVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Public version DTO. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionVersionPublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    publishQuestionVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Version published and now immutable. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionVersionPublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    uploadVersionFixture: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FixtureUploadInput"];
            };
        };
        responses: {
            /** @description Stored fixture metadata. The bytes are never returned. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FixtureUploadResult"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            413: components["responses"]["PayloadTooLarge"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    archiveQuestionVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Version archived. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    recordReferenceAttestation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReferenceAttestationInput"];
            };
        };
        responses: {
            /** @description Attestation recorded. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReferenceAttestationPublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getEvaluationPackage: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_version_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Immutable evaluation package. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["evaluation-package.schema"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    listAssignments: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                course_id?: components["schemas"]["Uuid"];
                section_id?: components["schemas"]["Uuid"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of assignments. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage"] & {
                        items?: components["schemas"]["AssignmentPublic"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
        };
    };
    createAssignment: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    course_id: components["schemas"]["Uuid"];
                    section_id: components["schemas"]["Uuid"];
                    title: string;
                    due_at?: components["schemas"]["Timestamp"];
                    question_version_ids: components["schemas"]["Uuid"][];
                };
            };
        };
        responses: {
            /** @description Assignment created. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
}
