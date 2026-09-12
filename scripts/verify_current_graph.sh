#!/usr/bin/env bash
#
# verify_current_graph.sh -- prove the graph can be VALIDATED and QUERIED without
# GraphDB, on the CURRENTLY published data (old http://medicaldevice.com/ namespace).
#
#   bash scripts/verify_current_graph.sh <dir-of-current-ttl>
#
# Recreates what the students checked in GraphDB (total triples, per-class counts,
# a real query) AND adds two checks GraphDB's import never surfaced:
#   * D1 -- MedicalDevice nodes that are orphans (type + label only, no edges)
#   * D4 -- date-looking literals that are untyped strings, not xsd:date
#
# Everything below is Apache Jena CLI: riot + arq. No triplestore, no GraphDB.
# Run it against whatever directory holds the current .ttl on the VM, e.g. the
# digest_ttl output or a lakeFS export. Locate candidates with:
#   find /srv /home -name '*.ttl' -size +100k 2>/dev/null | head
#
set -uo pipefail

SRC="${1:?usage: bash scripts/verify_current_graph.sh <dir-of-ttl>}"
shopt -s nullglob; ttls=("$SRC"/*.ttl); shopt -u nullglob
(( ${#ttls[@]} )) || { echo "error: no .ttl files in '$SRC'" >&2; exit 66; }

NS="http://medicaldevice.com/ontology/"
D=(); for t in "${ttls[@]}"; do D+=(--data "$t"); done

echo "#############################################################"
echo "# verify_current_graph  --  $(date)"
echo "# dir: $SRC   files: ${#ttls[@]}   namespace: $NS"
echo "#############################################################"

echo; echo "== 1. SYNTAX  (riot --validate) -- GraphDB import checks this too =="
riot --validate "${ttls[@]}" && echo "   PASS: every file is well-formed Turtle"

echo; echo "== 2. TRIPLE COUNT  (riot) -- recreates GraphDB's SELECT (COUNT(*)) =="
riot --count "${ttls[@]}"

echo; echo "== 3. INSTANCES PER CLASS  (arq) -- recreates GraphDB class browsing =="
arq "${D[@]}" --results=TSV --query <(cat <<RQ
PREFIX ex: <$NS>
SELECT ?type (COUNT(?s) AS ?n) WHERE { ?s a ?type } GROUP BY ?type ORDER BY DESC(?n)
RQ
)

echo; echo "== 4. COMPETENCY: recalls by classification (arq) =="
arq "${D[@]}" --results=TSV --query <(cat <<RQ
PREFIX ex: <$NS>
SELECT ?classification (COUNT(?r) AS ?n) WHERE {
  ?r a ex:RecallRecord ; ex:recallClassification ?classification
} GROUP BY ?classification ORDER BY DESC(?n)
RQ
)

echo; echo "== 5. TOP IT / DEFECT D1: orphan MedicalDevice nodes (no edge but type+label) =="
echo "   (GraphDB loaded these silently; expect a large non-zero count -- that IS the defect)"
arq "${D[@]}" --results=TSV --query <(cat <<RQ
PREFIX ex:   <$NS>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT (COUNT(?d) AS ?orphan_devices) WHERE {
  ?d a ex:MedicalDevice .
  FILTER NOT EXISTS {
    ?d ?p ?o . FILTER(?p != rdf:type && ?p != rdfs:label && ?p != ex:label)
  }
}
RQ
)

echo; echo "== 6. TOP IT / DEFECT D4: date-looking literals that are NOT xsd:date =="
echo "   (untyped 'YYYYMMDD'/'YYYY-MM-DD' strings; expect non-zero -- range queries can't work on these)"
arq "${D[@]}" --results=TSV --query <(cat <<RQ
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT (COUNT(*) AS ?untyped_date_literals) WHERE {
  ?s ?p ?v .
  FILTER(isLiteral(?v) && datatype(?v) != xsd:date)
  FILTER(REGEX(STR(?v), "^(19|20)[0-9]{2}-?[01][0-9]-?[0-3][0-9]\$"))
}
RQ
)

echo; echo "#############################################################"
echo "# DONE. Steps 1-4 match what GraphDB gave you (well-formed + queryable);"
echo "# steps 5-6 are validation GraphDB never did. All on Jena CLI, no GraphDB."
echo "#############################################################"
