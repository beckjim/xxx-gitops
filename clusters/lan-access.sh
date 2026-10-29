#!/usr/bin/env bash

lan_fqdn_matches_address() {
  local candidate="$1" address="$2" answers resolved label
  local valid=true matched=false
  local -a labels

  [[ ${#candidate} -le 253 && "$candidate" == *.* && "$candidate" != .* &&
    "$candidate" != *. && "$candidate" != *..* &&
    ! "$candidate" =~ ^[0-9.]+$ ]] || valid=false
  IFS=. read -r -a labels <<< "$candidate"
  for label in "${labels[@]}"; do
    [[ ${#label} -le 63 && "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] ||
      valid=false
  done

  if [[ "$valid" == true ]] && answers="$(getent ahostsv4 "$candidate")"; then
    while read -r resolved _; do
      [[ "$resolved" == "$address" ]] && matched=true
      [[ "$resolved" == "$address" ]] || valid=false
    done <<< "$answers"
  else
    valid=false
  fi

  [[ "$valid" == true && "$matched" == true ]]
}

lan_fqdn_candidates() {
  local address="$1" short_name domain records record name keyword
  local resolver_config="${2:-/etc/resolv.conf}"
  local -a fields domains=()

  if name="$(hostname -f)"; then
    printf '%s\n' "$name"
  fi
  if records="$(getent hosts "$address")"; then
    while read -r record; do
      read -r -a fields <<< "$record"
      printf '%s\n' "${fields[@]:1}"
    done <<< "$records"
  fi

  if ! short_name="$(hostname -s)"; then
    return
  fi
  if records="$(getent hosts "$short_name")"; then
    while read -r record; do
      read -r -a fields <<< "$record"
      printf '%s\n' "${fields[@]:1}"
    done <<< "$records"
  fi
  if domain="$(hostname -d)" && [[ -n "$domain" ]]; then
    domains+=("$domain")
  fi
  if [[ -r "$resolver_config" ]]; then
    while read -r keyword record; do
      case "$keyword" in
        search | domain)
          read -r -a fields <<< "$record"
          for domain in "${fields[@]}"; do
            [[ "$domain" != \#* && "$domain" != \;* ]] || break
            domains+=("$domain")
          done
          ;;
      esac
    done < "$resolver_config"
  fi
  for domain in "${domains[@]}"; do
    printf '%s.%s\n' "$short_name" "$domain"
  done
}

select_lan_fqdn() {
  local requested="$1" address="$2" candidate tool
  local -A seen=()

  for tool in hostname getent; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required for LAN hostname discovery."
  done

  if [[ -n "$requested" ]]; then
    candidate="${requested%.}"
    candidate="${candidate,,}"
    if lan_fqdn_matches_address "$candidate" "$address"; then
      printf '%s\n' "$candidate"
      return
    fi
    fail "FQDN $requested must be a valid DNS name resolving only to LAN IPv4 $address."
  fi

  while IFS= read -r candidate; do
    candidate="${candidate%.}"
    candidate="${candidate,,}"
    [[ -n "$candidate" ]] || continue
    [[ -z "${seen[$candidate]:-}" ]] || continue
    seen["$candidate"]=true
    if lan_fqdn_matches_address "$candidate" "$address"; then
      printf '%s\n' "$candidate"
      return
    fi
  done < <(lan_fqdn_candidates "$address")

  echo "WARNING: No FQDN from hostname, reverse lookup, or DNS search domains resolves only to LAN IPv4 $address; using IP access only. Set API_SERVER_FQDN or ARGOCD_FQDN after configuring LAN DNS." >&2
}
