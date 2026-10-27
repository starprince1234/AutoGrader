/**
 * AUTO-GENERATED FROM openapi/b4.yaml — DO NOT EDIT BY HAND.
 *
 * Regenerate with: pnpm api:generate
 * Source of truth: MAIN/contracts/** (see docs/adr/ADR-0002-contract-generated-api-client.md).
 * Any hand edit is reported as contract drift by `pnpm api:check`.
 */

export interface paths {
    "/api/v1/auth/session": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Authenticate and open a session
         * @description Verifies credentials, returns a short-lived Ed25519 access token, and sets the
         *     refresh credential as an HttpOnly + Secure + SameSite cookie. The refresh
         *     credential is never returned in the response body. Failures do not reveal
         *     whether the principal exists.
         */
        post: operations["createSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/session/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rotate the refresh credential and issue a new access token */
        post: operations["refreshSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/session/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Invalidate the current session */
        delete: operations["deleteSession"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/jwks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Publish the Ed25519 public keys
         * @description Every module verifies tokens locally against this key set instead of calling
         *     B4 on the request path. Keys are identified by `kid` and rotated from the
         *     runbook procedure.
         */
        get: operations["getJwks"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/registrations/code": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Send the e-mail verification code for a self-service registration
         * @description Self-service registration is open (no approval step): the caller proves the
         *     mailbox, the identity is checked against the administrator's allowlist, and the
         *     account created by `createRegistration` can sign in immediately.
         *
         *     This operation validates the claimed identity (13-digit student number, full
         *     name present in the allowlist) **before** sending anything, so a code is never
         *     mailed for an identity that cannot register. It stores only a SHA-256 digest of
         *     the code, bound to the exact registration intent, and it never stores the
         *     password. A missing or failing SMTP configuration is `503`: the endpoint never
         *     degrades to skipping verification.
         */
        post: operations["requestRegistrationCode"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/registrations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Complete a self-service registration with the e-mailed code
         * @description Verifies the code against the intent stored by `requestRegistrationCode`, then
         *     creates the account in one transaction: `b4.users`, `b4.credentials` (Argon2id),
         *     the platform `student` role, and a link for every roster enrollment that already
         *     carries this student number but no user yet. The account is usable immediately —
         *     there is no approval state.
         *
         *     The request repeats the e-mail only as a lookup key; the username, student number
         *     and display name come from the stored intent so that "what was verified" and
         *     "what is created" cannot diverge.
         */
        post: operations["createRegistration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/registration-allowlists": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Report the active registration allowlist (admin only) */
        get: operations["getRegistrationAllowlist"];
        put?: never;
        /**
         * Replace the registration allowlist from a CSV file (admin only)
         * @description The CSV carries the identity anchor used by self-service registration: a
         *     `姓名`/`学号` header (a `name`/`student_number` header is accepted too) and one
         *     row per student. The upload **replaces** the allowlist in one transaction: rows
         *     absent from the new file can no longer register, and previous batches stay
         *     recorded for traceability.
         *
         *     Encoding: UTF-8 (with or without BOM) or GBK/GB18030. A file that decodes into
         *     something other than 13-digit student numbers is rejected rather than imported
         *     partially. Columns are located by header name, never by position.
         */
        post: operations["replaceRegistrationAllowlist"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List platform users (admin only) */
        get: operations["listUsers"];
        put?: never;
        /** Create a platform user (admin only) */
        post: operations["createUser"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/{user_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Deactivate a user (admin only)
         * @description **Deactivation, not deletion.** The row, its historical records and the accounts it
         *     owns stay in place; `is_active` becomes `false` and every open session of that user is
         *     revoked, so the account can no longer sign in. An administrator whose own row this is
         *     gets `409`: deactivating yourself would end the session that is performing the request
         *     and can leave the platform without an active administrator. Re-activation has no
         *     endpoint here — it is deliberately not invented by a client.
         */
        delete: operations["deleteUser"];
        options?: never;
        head?: never;
        /** Update a user or reassign roles (admin only) */
        patch: operations["updateUser"];
        trace?: never;
    };
    "/api/v1/courses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List courses within the caller's authorization scope */
        get: operations["listCourses"];
        put?: never;
        /** Create a course (teacher or admin) */
        post: operations["createCourse"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/courses/{course_id}/sections": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                course_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /** List sections (课序) of a course */
        get: operations["listSections"];
        put?: never;
        /** Create a section (teacher or admin) */
        post: operations["createSection"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sections/{section_id}/enrollments": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                section_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /**
         * List the roster of a section
         * @description Returns student identity fields only to callers authorized for the section.
         */
        get: operations["listEnrollments"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/enrollments/{enrollment_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                enrollment_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Remove a roster entry */
        delete: operations["deleteEnrollment"];
        options?: never;
        head?: never;
        /**
         * Edit a roster entry (RQ-B-1-20)
         * @description Requires write permission for the section. Concurrency is guarded by
         *     expected_updated_at; a mismatch returns 409 instead of overwriting. Every
         *     successful edit writes an audit record with the actor, target and a digest
         *     of the before/after values.
         */
        patch: operations["updateEnrollment"];
        trace?: never;
    };
    "/api/v1/roster-imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Upload a roster file and start parsing (RQ-B-1-10)
         * @description Accepts a CSV or XLSX roster. Parsing never guesses: when a column cannot be
         *     mapped unambiguously the import stops in `mapping_required` and waits for a
         *     human decision. No roster row is persisted before confirmation.
         */
        post: operations["createRosterImport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roster-imports/{roster_import_id}": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roster_import_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        /** Read the parse preview and per-row validation result */
        get: operations["getRosterImport"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roster-imports/{roster_import_id}/mapping": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roster_import_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        /**
         * Confirm or correct the column mapping
         * @description Required when the import is in `mapping_required`; the mapping is recorded for traceability.
         */
        put: operations["setRosterColumnMapping"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roster-imports/{roster_import_id}/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roster_import_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Commit the previewed roster atomically (RQ-B-1-10)
         * @description Idempotent: repeating the confirmation does not duplicate rows. Atomic: either
         *     every accepted row is written or none is. Emits
         *     `autograder.v1.roster.import.completed` on success; the payload carries no PII.
         */
        post: operations["confirmRosterImport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/audit-records": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Query audit records (admin or authorized teacher) */
        get: operations["listAuditRecords"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/modules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Runtime module registry (RQ-B-4-40)
         * @description Reports the modules the composition host actually loaded, so "plug and play"
         *     is observable at runtime rather than asserted in prose. The registry content
         *     must agree with each module's module.yaml and SDK descriptor; a disagreement
         *     is reported as a failure, never smoothed over.
         */
        get: operations["listRegisteredModules"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/modules/{module}/health": {
        parameters: {
            query?: never;
            header?: never;
            path: {
                module: string;
            };
            cookie?: never;
        };
        /** Per-module health reported through the host */
        get: operations["getModuleHealth"];
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
         * @description One row of a section's roster. Recorded by CHG-0007: the operation previously
         *     returned an undocumented body ("Page of enrollments.").
         *
         *     `student_id` was added by the same change because its absence made a required
         *     feature impossible: B2's grade rows identify a student only by platform user id,
         *     while the roster exposed only `student_number`/`display_name` — and B2 legitimately
         *     cannot supply those (it owns only the `b2` schema, so its `student_number` and
         *     `display_name` are always null by design). With no shared key, a client could not
         *     answer "who has not submitted yet" without dumping the admin-only user list.
         *     `student_id` is the platform user id and is **nullable**: a roster row that was
         *     never matched to a user account has none (the column is nullable in `b4`).
         */
        EnrollmentPublic: {
            enrollment_id: components["schemas"]["Uuid"];
            section_id: components["schemas"]["Uuid"];
            student_id?: components["schemas"]["Uuid"] | null;
            student_number: string;
            display_name: string;
            is_active: boolean;
            updated_at?: components["schemas"]["Timestamp"];
        };
        UserSummary: {
            user_id: components["schemas"]["Uuid"];
            /** @description Login identifier. Required in the summary: it is the only field by which an administrator can recognise or look up an account, and an admin screen that cannot show it is unusable (RQ-B-4-50). */
            username: string;
            display_name: string;
            student_number?: string | null;
            roles: components["schemas"]["Role"][];
            /**
             * @description Whether the account may sign in. `deleteUser` is a **soft** deactivation, so this
             *     is the only field that distinguishes a deactivated account from a usable one: an
             *     admin screen that omits it has no way to say whether `204` actually took effect,
             *     and RQ-B-4-50 requires the account state to be visible.
             */
            is_active: boolean;
            /**
             * @description Last modification. It is the value `updateUser.expected_updated_at` is compared
             *     against, so it is required here: without it a client can only send a timestamp it
             *     guessed, and concurrent administrator edits either always conflict or never do.
             */
            updated_at: components["schemas"]["Timestamp"];
            created_at?: components["schemas"]["Timestamp"];
        };
        /**
         * @description A course as the API returns it, for reads and for the create response. The create
         *     response previously carried only `course_id`, so a client could not show the new
         *     course without a second round trip.
         */
        CoursePublic: {
            course_id: components["schemas"]["Uuid"];
            course_code: string;
            title: string;
        };
        /**
         * @description A section (课序) of a course. Defined here because the read and create responses
         *     had no schema, which forced the client to guess which key carried the identifier
         *     and which one carried the label.
         */
        SectionPublic: {
            section_id: components["schemas"]["Uuid"];
            course_id: components["schemas"]["Uuid"];
            section_code: string;
            term?: string | null;
            created_at?: components["schemas"]["Timestamp"] | null;
        };
        /**
         * @description Parse preview for a roster import. `database.schema` of B4 is untouched while
         *     the import sits in `previewed`. Rows that fail validation appear in `rejected`
         *     with a reason; they are never dropped silently.
         */
        RosterImportPreview: {
            roster_import_id: components["schemas"]["Uuid"];
            /** @enum {string} */
            status: "uploaded" | "parsed" | "mapping_required" | "validating" | "previewed" | "committed" | "rejected" | "failed_parse";
            course_id: components["schemas"]["Uuid"];
            section_id: components["schemas"]["Uuid"];
            detected_columns: string[];
            proposed_mapping?: {
                [key: string]: string;
            };
            accepted_rows?: components["schemas"]["RosterRowPreview"][];
            rejected_rows?: components["schemas"]["RosterRowPreview"][];
            created_at?: components["schemas"]["Timestamp"];
        };
        RosterRowPreview: {
            row_number: number;
            /** @description Normalized field values for review. Contains student identity fields; returned only to authorized callers. */
            values?: {
                [key: string]: unknown;
            };
            /** @description Why the row was rejected; required for entries in rejected_rows. */
            reason?: string | null;
        };
        /** @description One loaded module as observed at runtime by the composition host. */
        RegisteredModule: {
            module: string;
            contract_version: string;
            provides: string[];
            requires: string[];
            routes: string[];
            publishes: string[];
            subscribes: string[];
            /** @enum {string} */
            database_schema: "none" | "b2" | "b3" | "b4";
            migration_head: string;
            healthy: boolean;
            detail?: string;
        };
        /** @enum {string} */
        Role: "student" | "teacher" | "assistant" | "admin";
        /**
         * Format: uuid
         * @description UUID v4 (RFC 9562), generated by the owning module. See MAIN/docs/api/identifiers.md.
         */
        Uuid: string;
        /** @description Authorization scope resolved server-side from the verified token. Never trusted from the client. */
        CourseScope: {
            course_id: components["schemas"]["Uuid"];
            section_id?: components["schemas"]["Uuid"];
        };
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
        /** @description Schema-valid request rejected by a business rule. */
        UnprocessableEntity: {
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
        /** @description Authenticated, but the role or course scope does not permit the operation. */
        Forbidden: {
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
        /** @description Authenticated and authorized, but the resource does not exist. */
        NotFound: {
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
        /** @description Alternative to a body-carried idempotency key for create operations. */
        IdempotencyKey: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    createSession: {
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
                "application/json": {
                    username: string;
                    /** Format: password */
                    password: string;
                };
            };
        };
        responses: {
            /** @description Session created. */
            200: {
                headers: {
                    /** @description HttpOnly; Secure; SameSite refresh credential. Never exposed to JavaScript. */
                    "Set-Cookie"?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        /** @description Short-lived Ed25519 JWT. */
                        access_token: string;
                        /** @constant */
                        token_type: "Bearer";
                        expires_in: number;
                        /**
                         * @description Login identifier of the authenticated principal. The token itself
                         *     carries only `sub` (a UUID) by design, so without this field a
                         *     client cannot say *who* is signed in after a refresh -- the header
                         *     rendered "-" until integration caught it.
                         */
                        username: string;
                        roles: components["schemas"]["Role"][];
                        scope: components["schemas"]["CourseScope"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            422: components["responses"]["UnprocessableEntity"];
            429: components["responses"]["TooManyRequests"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    refreshSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Access token reissued; the refresh credential is rotated. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        access_token: string;
                        /** @constant */
                        token_type: "Bearer";
                        expires_in: number;
                        /** @description See createSession: the principal's login name, so a refreshed page can still show who is signed in. */
                        username: string;
                        roles: components["schemas"]["Role"][];
                        scope: components["schemas"]["CourseScope"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
        };
    };
    deleteSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Session invalidated. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
        };
    };
    getJwks: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description JWK Set. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        keys: ({
                            /** @constant */
                            kty: "OKP";
                            /** @constant */
                            crv: "Ed25519";
                            kid: string;
                            x: string;
                            /** @constant */
                            use?: "sig";
                            /** @constant */
                            alg?: "EdDSA";
                        } & {
                            [key: string]: unknown;
                        })[];
                    };
                };
            };
        };
    };
    requestRegistrationCode: {
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
                "application/json": {
                    /** @description Login identifier. Lowercase; must not contain "@". */
                    username: string;
                    /** Format: email */
                    email: string;
                    student_number: string;
                    display_name: string;
                };
            };
        };
        responses: {
            /** @description Code sent (or the same answer when one was already in flight). */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        expires_in_seconds: number;
                        resend_after_seconds: number;
                    };
                };
            };
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
            429: components["responses"]["TooManyRequests"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    createRegistration: {
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
                "application/json": {
                    /** Format: email */
                    email: string;
                    code: string;
                    /** Format: password */
                    password: string;
                };
            };
        };
        responses: {
            /** @description Account created; the caller can sign in immediately. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        /** Format: uuid */
                        user_id: string;
                        username: string;
                    };
                };
            };
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
            429: components["responses"]["TooManyRequests"];
        };
    };
    getRegistrationAllowlist: {
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
        requestBody?: never;
        responses: {
            /** @description The active batch, or nulls when none was ever imported. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        /** Format: uuid */
                        batch_id: string | null;
                        filename: string | null;
                        row_count: number;
                        /** Format: date-time */
                        uploaded_at: string | null;
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
        };
    };
    replaceRegistrationAllowlist: {
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
                "multipart/form-data": {
                    /** Format: binary */
                    file: string;
                };
            };
        };
        responses: {
            /** @description Allowlist replaced. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        /** Format: uuid */
                        batch_id: string;
                        row_count: number;
                        replaced_row_count: number;
                        content_sha256: string;
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            413: components["responses"]["PayloadTooLarge"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    listUsers: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                /**
                 * @description Restrict the page to holders of that **platform** role (a course-scoped grant is
                 *     not a platform role). The filter selects rows; it never widens what a row shows.
                 *     There is no name/username search: the contract declares none, and a client that
                 *     pulled the whole table to filter locally would spread identity data for a feature
                 *     the server does not offer.
                 */
                role?: components["schemas"]["Role"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of users. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage"] & {
                        items?: components["schemas"]["UserSummary"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
        };
    };
    createUser: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    display_name: string;
                    username: string;
                    student_number?: string | null;
                    /** Format: email */
                    email?: string | null;
                    roles: components["schemas"]["Role"][];
                    /**
                     * Format: password
                     * @description Hashed with Argon2id or bcrypt at rest; never logged.
                     */
                    password?: string;
                };
            };
        };
        responses: {
            /**
             * @description User created. The body carries the identity of the new account so an admin
             *     screen can name what it created (`RQ-B-4-50`): it is not a credential and it
             *     is not a second read of the user — a client that wants the full row still
             *     re-reads the list.
             */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        user_id: components["schemas"]["Uuid"];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    deleteUser: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description User deactivated; an audit record is written. */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            /**
             * @description Refused because the target is the caller's own account. Deactivation revokes the
             *     caller's sessions, so the operation cannot be reported back to the actor that
             *     asked for it; a second administrator has to perform it.
             */
            409: components["responses"]["Conflict"];
        };
    };
    updateUser: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    display_name?: string;
                    /** Format: email */
                    email?: string | null;
                    roles?: components["schemas"]["Role"][];
                    /** @description Optimistic concurrency guard; a mismatch returns 409 rather than silently overwriting. */
                    expected_updated_at?: components["schemas"]["Timestamp"];
                };
            };
        };
        responses: {
            /** @description User updated; an audit record is written. */
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
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    listCourses: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of courses. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage"] & {
                        items?: components["schemas"]["CoursePublic"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
        };
    };
    createCourse: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    course_code: string;
                    title: string;
                };
            };
        };
        responses: {
            /** @description Course created; the body is the stored course. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CoursePublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            409: components["responses"]["Conflict"];
        };
    };
    listSections: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                course_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Sections of the course, ordered by section code. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        items: components["schemas"]["SectionPublic"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    createSection: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                course_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    section_code: string;
                    term?: string | null;
                };
            };
        };
        responses: {
            /** @description Section created; the body is the stored section. */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SectionPublic"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
        };
    };
    listEnrollments: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
            };
            header?: never;
            path: {
                section_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of enrollments. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage"] & {
                        items?: components["schemas"]["EnrollmentPublic"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    deleteEnrollment: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                enrollment_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Enrollment removed; an audit record is written. */
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
    updateEnrollment: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                enrollment_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    student_number?: string;
                    display_name?: string;
                    expected_updated_at?: components["schemas"]["Timestamp"];
                };
            };
        };
        responses: {
            /** @description Enrollment updated. */
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
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    createRosterImport: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": {
                    /** Format: binary */
                    file: string;
                    course_id: components["schemas"]["Uuid"];
                    section_id: components["schemas"]["Uuid"];
                };
            };
        };
        responses: {
            /** @description Import accepted for parsing. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RosterImportPreview"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            413: components["responses"]["PayloadTooLarge"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    getRosterImport: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roster_import_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Current import state and preview. Rejected rows are always listed, never dropped silently. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RosterImportPreview"];
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
        };
    };
    setRosterColumnMapping: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                roster_import_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    /** @description Map of target field name to source column header. */
                    columns: {
                        [key: string]: string;
                    };
                };
            };
        };
        responses: {
            /** @description Mapping accepted; validation runs again. */
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
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    confirmRosterImport: {
        parameters: {
            query?: never;
            header?: {
                /** @description Alternative to a body-carried idempotency key for create operations. */
                "Idempotency-Key"?: components["parameters"]["IdempotencyKey"];
            };
            path: {
                roster_import_id: components["schemas"]["Uuid"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Commit accepted. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            404: components["responses"]["NotFound"];
            409: components["responses"]["Conflict"];
            422: components["responses"]["UnprocessableEntity"];
        };
    };
    listAuditRecords: {
        parameters: {
            query?: {
                cursor?: components["parameters"]["Cursor"];
                limit?: components["parameters"]["Limit"];
                course_id?: components["schemas"]["Uuid"];
                from?: components["schemas"]["Timestamp"];
                to?: components["schemas"]["Timestamp"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Page of audit records. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
        };
    };
    listRegisteredModules: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Loaded modules and their declared capabilities. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        modules: components["schemas"]["RegisteredModule"][];
                    };
                };
            };
            401: components["responses"]["Unauthorized"];
            403: components["responses"]["Forbidden"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
    getModuleHealth: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                module: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Module health report. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            404: components["responses"]["NotFound"];
            503: components["responses"]["ServiceUnavailable"];
        };
    };
}
