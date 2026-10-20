#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf '[network-audit] ERROR: %s\n' "$*" >&2
    exit 1
}

[[ "$#" == 5 ]] || fail "run id, attempt, target SHA, base SHA, and repository URL are required"
run_id="$1"
attempt="$2"
target_sha="$3"
base_sha="$4"
repository_url="$5"
[[ "$run_id" =~ ^[0-9]+$ && "$attempt" =~ ^[1-9][0-9]*$ ]] || fail "invalid workflow run identity"
[[ "$target_sha" =~ ^[a-f0-9]{40}$ && "$base_sha" =~ ^[a-f0-9]{40}$ ]] || fail "invalid commit SHA"
[[ "$repository_url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$ ]] || \
    fail "repository URL is not a GitHub HTTPS URL"
bench_dir="/tmp/autograder-fetch-bench-${run_id}-${attempt}"
[[ ! -e "$bench_dir" && ! -L "$bench_dir" ]] || fail "benchmark path already exists"
install -d -o root -g root -m 0700 "$bench_dir"
cleanup() {
    local status=$?
    trap - EXIT
    if [[ "$bench_dir" == "/tmp/autograder-fetch-bench-${run_id}-${attempt}" && \
        -d "$bench_dir" && ! -L "$bench_dir" ]]; then
        rm -rf -- "$bench_dir"
    fi
    exit "$status"
}
trap cleanup EXIT

proxy_url=http://127.0.0.1:7893
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY \
    http_proxy https_proxy all_proxy NO_PROXY no_proxy
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0

now_ns() { date +%s%N; }
elapsed_ms() { awk -v start="$1" -v end="$2" 'BEGIN { printf "%.0f", (end - start) / 1000000 }'; }

git_route() {
    local route="$1" limit="$2" rc
    shift 2
    if [[ "$route" == direct ]]; then
        if env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
            -u http_proxy -u https_proxy -u all_proxy -u NO_PROXY -u no_proxy \
            timeout "$limit" git -c http.proxy= "$@"; then
            rc=0
        else
            rc=$?
        fi
    else
        if env HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url" ALL_PROXY="$proxy_url" \
            http_proxy="$proxy_url" https_proxy="$proxy_url" all_proxy="$proxy_url" \
            NO_PROXY= no_proxy= timeout "$limit" \
            git -c "http.proxy=$proxy_url" "$@"; then
            rc=0
        else
            rc=$?
        fi
    fi
    return "$rc"
}

curl_probe() {
    local route="$1" sample="$2" start end result rc
    start="$(now_ns)"
    if [[ "$route" == direct ]]; then
        if result="$(env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
            -u http_proxy -u https_proxy -u all_proxy -u NO_PROXY -u no_proxy \
            timeout 25s curl -q --noproxy '*' --silent --show-error --head --location \
            --connect-timeout 8 --max-time 20 --output /dev/null \
            --write-out 'http=%{http_code} dns_s=%{time_namelookup} connect_s=%{time_connect} tls_s=%{time_appconnect} total_s=%{time_total}' \
            https://github.com 2>/dev/null)"; then rc=0; else rc=$?; result=""; fi
    else
        if result="$(env HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url" ALL_PROXY="$proxy_url" \
            http_proxy="$proxy_url" https_proxy="$proxy_url" all_proxy="$proxy_url" \
            NO_PROXY= no_proxy= timeout 25s curl -q --proxy "$proxy_url" --silent --show-error \
            --head --location --connect-timeout 8 --max-time 20 --output /dev/null \
            --write-out 'http=%{http_code} dns_s=%{time_namelookup} connect_s=%{time_connect} tls_s=%{time_appconnect} total_s=%{time_total}' \
            https://github.com 2>/dev/null)"; then rc=0; else rc=$?; result=""; fi
    fi
    end="$(now_ns)"
    printf 'probe route=%s kind=https-head sample=%s result=%s elapsed_ms=%s %s\n' \
        "$route" "$sample" "$([[ "$rc" == 0 ]] && printf ok || printf failed)" \
        "$(elapsed_ms "$start" "$end")" "${result:-exit_$rc}"
}

classify_git_error() {
    local error_file="$1"
    if grep -Eqi 'authentication failed|HTTP 401|HTTP 403|repository not found|could not read Username' "$error_file"; then
        printf auth_error
    elif grep -Eqi 'could not resolve|failed to connect|timed out|TLS|SSL|proxy' "$error_file"; then
        printf network_error
    else
        printf git_error
    fi
}

ls_remote_probe() {
    local route="$1" sample="$2" start end rc advertised_sha
    local out="$bench_dir/ls-remote-${route}-${sample}.out"
    local err="$bench_dir/ls-remote-${route}-${sample}.err"
    start="$(now_ns)"
    if git_route "$route" 25s ls-remote "$repository_url" refs/heads/main >"$out" 2>"$err"; then
        end="$(now_ns)"
        advertised_sha="$(awk '$2 == "refs/heads/main" { print $1; exit }' "$out")"
        printf 'probe route=%s kind=git-ls-remote sample=%s result=ok elapsed_ms=%s main_matches_target=%s\n' \
            "$route" "$sample" "$(elapsed_ms "$start" "$end")" \
            "$([[ "$advertised_sha" == "$target_sha" ]] && printf yes || printf no)"
        return 0
    else
        rc=$?
    fi
    end="$(now_ns)"
    printf 'probe route=%s kind=git-ls-remote sample=%s result=failed exit=%s category=%s elapsed_ms=%s\n' \
        "$route" "$sample" "$rc" "$(classify_git_error "$err")" \
        "$(elapsed_ms "$start" "$end")"
    return "$rc"
}

measure_fetch() {
    local route="$1" stage="$2" sha="$3" limit="$4" repo="$5"
    local start end rc err="$bench_dir/fetch-$1-$2.err"
    start="$(now_ns)"
    if git_route "$route" "$limit" -C "$repo" fetch --quiet --filter=blob:none \
        --depth=1 origin "$sha" 2>"$err"; then
        end="$(now_ns)"
        printf 'fetch route=%s stage=%s result=ok elapsed_ms=%s object_store_bytes=%s\n' \
            "$route" "$stage" "$(elapsed_ms "$start" "$end")" \
            "$(du -sb "$repo/objects" | awk 'NR == 1 { print $1 }')"
        return 0
    else
        rc=$?
    fi
    end="$(now_ns)"
    printf 'fetch route=%s stage=%s result=failed exit=%s category=%s elapsed_ms=%s\n' \
        "$route" "$stage" "$rc" "$(classify_git_error "$err")" \
        "$(elapsed_ms "$start" "$end")"
    return "$rc"
}

audit_route() {
    local route="$1" sample auth_failure=false rc start end
    local repo="$bench_dir/$route-cache.git" archive="$bench_dir/$route-runtime.tar.gz"
    local entries="$bench_dir/$route-runtime.entries" err="$bench_dir/archive-$route.err"

    for sample in 1 2 3; do
        if ls_remote_probe "$route" "$sample"; then
            :
        else
            rc=$?
            if grep -Eqi 'authentication failed|HTTP 401|HTTP 403|repository not found|could not read Username' \
                "$bench_dir/ls-remote-${route}-${sample}.err"; then
                auth_failure=true
                break
            fi
        fi
    done
    if [[ "$auth_failure" == true ]]; then
        printf 'route_summary route=%s result=auth_blocked; fetch_not_attempted=true\n' "$route"
        return 0
    fi

    git init --bare --quiet "$repo"
    git -C "$repo" remote add origin "$repository_url"
    git -C "$repo" config extensions.partialClone origin
    git -C "$repo" config remote.origin.promisor true
    git -C "$repo" config remote.origin.partialclonefilter blob:none

    if ! measure_fetch "$route" baseline "$base_sha" 90s "$repo"; then
        printf 'route_summary route=%s result=baseline_fetch_failed\n' "$route"
        return 0
    fi
    if ! measure_fetch "$route" incremental "$target_sha" 90s "$repo"; then
        printf 'route_summary route=%s result=incremental_fetch_failed\n' "$route"
        return 0
    fi

    start="$(now_ns)"
    if git_route "$route" 180s -C "$repo" archive --format=tar.gz \
        --output="$archive" "$target_sha" B2 B3 B4 B5 MAIN 2>"$err"; then
        end="$(now_ns)"
        if ! tar -tzf "$archive" >"$entries"; then
            printf 'archive route=%s result=invalid_archive\n' "$route"
            return 0
        fi
        grep -qx 'MAIN/contracts/manifest.schema.json' "$entries" || \
            fail "runtime archive is missing MAIN/contracts/manifest.schema.json"
        if grep -Eq '^MAIN/references/[^/]+\.pptx$' "$entries"; then
            fail "runtime archive contains excluded course PPTX files"
        fi
        printf 'archive route=%s result=ok materialization_ms=%s bytes=%s references_excluded=true\n' \
            "$route" "$(elapsed_ms "$start" "$end")" "$(stat -c %s "$archive")"
    else
        rc=$?
        end="$(now_ns)"
        printf 'archive route=%s result=failed exit=%s category=%s elapsed_ms=%s\n' \
            "$route" "$rc" "$(classify_git_error "$err")" \
            "$(elapsed_ms "$start" "$end")"
        printf 'route_summary route=%s result=archive_materialization_failed\n' "$route"
        return 0
    fi

    for sample in 1 2 3; do
        measure_fetch "$route" "same_sha_$sample" "$target_sha" 30s "$repo" || true
    done
    route_successes=$((route_successes + 1))
    printf 'route_summary route=%s result=fetch_and_archive_succeeded\n' "$route"
}

for command_name in git curl timeout tar du stat date awk grep; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required benchmark command is missing: $command_name"
done

printf 'audit_context target_sha=%s base_sha=%s architecture=%s git=%s existing_proxy=%s\n' \
    "$target_sha" "$base_sha" "$(uname -m)" "$(git --version)" "$proxy_url"
route_successes=0
for route in direct proxy; do
    for sample in 1 2 3; do
        curl_probe "$route" "$sample"
    done
    audit_route "$route"
done

[[ "$route_successes" -ge 1 ]] || fail "GitHub fetch and runtime archive failed over both direct and proxy routes"
printf 'audit_summary successful_routes=%s temp_cleanup=on_exit token_persisted=false\n' "$route_successes"
