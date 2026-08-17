# -*- coding: utf-8 -*-
"""HTSA-Explorer computation service and browser application."""

import os
import re
import json
import uuid
import time
import hashlib
from datetime import datetime, timezone
from flask import Flask, Response, jsonify, render_template, request
import logging
#from utils import ossoss, VVGreedy, PPGreedy, sim, build_summary_tree

import heapq
import networkx as nx
import numpy as np

logger = logging.getLogger(__name__)


def to_nx_graph(G_in, node_dict=None):
    """Convert a serialized graph payload to a NetworkX directed graph."""
    logger.debug("Converting graph input of type %s", type(G_in).__name__)

    if isinstance(G_in, (nx.DiGraph, nx.Graph)):
        logger.debug("Graph input is already a NetworkX graph")
        return G_in.copy()

    G = nx.DiGraph()

    # When no serialized graph is supplied, initialize it from node keys.
    if not isinstance(G_in, dict):
        logger.debug("Graph input is absent; constructing nodes from node_dict")
        if node_dict:
            G.add_nodes_from(node_dict.keys())
        return G

    nodes = G_in.get('nodes', [])
    edges = G_in.get('edges', [])

    logger.debug("Graph payload contains %d nodes and %d edges", len(nodes), len(edges))

    # Nodes may be encoded either as an attribute map or an identifier list.
    if isinstance(nodes, dict):
        logger.debug("Graph nodes are encoded as a dictionary")
        for nid, attrs in nodes.items():
            # Merge normalized time-series attributes when available.
            if node_dict and nid in node_dict:
                ts, val, info = node_dict[nid]
                attrs = {**(attrs or {}), 'time_series': ts, 'value': val, **(info or {})}

            G.add_node(nid, **(attrs or {}))

    elif isinstance(nodes, list):
        logger.debug("Graph nodes are encoded as a list")
        for nid in nodes:
            attrs = {}
            if node_dict and nid in node_dict:
                ts, val, info = node_dict[nid]
                attrs = {'time_series': ts, 'value': val, **(info or {})}
            G.add_node(nid, **attrs)

    else:
        logger.warning("Unexpected graph node encoding: %s", type(nodes).__name__)

    # Edges are encoded as two-element lists or tuples.
    if isinstance(edges, list):
        logger.debug("Graph edges are encoded as a list")
        for e in edges:
            if isinstance(e, (list, tuple)) and len(e) >= 2:
                G.add_edge(e[0], e[1])
            else:
                logger.warning("Unexpected edge payload: %r", e)
    else:
        logger.warning("Unexpected graph edge encoding: %s", type(edges).__name__)

    return G



def oss(G, node_dict, node_x, method="FDS", a=0.0):
    BEAM_SIZE = None
    try:
        descendants = nx.descendants(G, node_x)
    except Exception:
        return set(), 0.0
    target_nodes = descendants.union({node_x})
    sim_cache = {}
    ts_x = node_dict[node_x][0]
    for other_node in target_nodes:
        if other_node == node_x:
            sim_cache[other_node] = 1.0
        else:
            ts_other = node_dict[other_node][0]
            sim_cache[other_node] = sim(ts_x, ts_other, method, a=a)
    dp = {}
    def pareto3(table):
        items = []
        for (s, t), (sim_sum, sel) in table.items():
            items.append((s, t, sim_sum, sel))
        items.sort(key=lambda x: (x[0], -x[1], -x[2]))
        kept = []
        for s, t, sim_sum, sel in items:
            dominated = False
            for s2, t2, sim2, _ in kept:
                if (s2 <= s) and (t2 >= t) and (sim2 >= sim_sum) and ((s2 < s) or (t2 > t) or (sim2 > sim_sum)):
                    dominated = True
                    break
            if not dominated:
                kept.append((s, t, sim_sum, sel))
        out = {}
        for s, t, sim_sum, sel in kept:
            prev = out.get((s, t))
            if (prev is None) or (sim_sum > prev[0]):
                out[(s, t)] = (sim_sum, sel)
        return out
    def merge_with_child_using_heap(cur, DPu, u):
        next_conf = {}
        for (s1, t1), (sim1, sel1) in cur.items():
            prev = next_conf.get((s1, t1))
            if (prev is None) or (sim1 > prev[0]):
                next_conf[(s1, t1)] = (sim1, sel1)
        if BEAM_SIZE is None:
            for (s1, t1), (sim1, sel1) in cur.items():
                for (s2, t2), (sim2, _sel2) in DPu.items():
                    s_ = s1 + s2
                    t_ = t1 + t2
                    sim_ = sim1 + sim2
                    sel_ = sel1 + [(u, (s2, t2))]
                    prev = next_conf.get((s_, t_))
                    if (prev is None) or (sim_ > prev[0]):
                        next_conf[(s_, t_)] = (sim_, sel_)
            return pareto3(next_conf)
        cur_list = [ (s1, t1, sim1, sel1) for (s1,t1),(sim1,sel1) in cur.items() ]
        dpu_list = [ (s2, t2, sim2) for (s2,t2),(sim2,_sel2) in DPu.items() ]
        if not cur_list or not dpu_list:
            return pareto3(next_conf)
        cur_list.sort(key=lambda x: (-x[2], -x[1], x[0]))
        dpu_list.sort(key=lambda x: (-x[2], -x[1], x[0]))
        def key(i, j):
            return cur_list[i][2] + dpu_list[j][2]
        heap = [ (-(key(0,0)), 0, 0) ]
        seen = { (0,0) }
        produced = 0
        while heap and produced < BEAM_SIZE:
            _negk, i, j = heapq.heappop(heap)
            s1, t1, sim1, sel1 = cur_list[i]
            s2, t2, sim2 = dpu_list[j]
            s_ = s1 + s2
            t_ = t1 + t2
            sim_ = sim1 + sim2
            sel_ = sel1 + [(u, (s2, t2))]
            prev = next_conf.get((s_, t_))
            if (prev is None) or (sim_ > prev[0]):
                next_conf[(s_, t_)] = (sim_, sel_)
            produced += 1
            if i + 1 < len(cur_list) and (i+1, j) not in seen:
                seen.add((i+1, j))
                heapq.heappush(heap, (-(key(i+1, j)), i+1, j))
            if j + 1 < len(dpu_list) and (i, j+1) not in seen:
                seen.add((i, j+1))
                heapq.heappush(heap, (-(key(i, j+1)), i, j+1))
        return pareto3(next_conf)
    def build(x):
        children = [u for u in G.successors(x) if u in target_nodes]
        for u in children:
            build(u)
        base = {(1, node_dict[x][1]): (sim_cache[x], [])}
        cur = base
        for u in children:
            DPu = dp[u]
            cur = merge_with_child_using_heap(cur, DPu, u)
        dp[x] = cur
    def reconstruct(x, key):
        S = {x}
        _, sel_list = dp[x][key]
        for (u, key_u) in sel_list:
            S |= reconstruct(u, key_u)
        return S
    build(node_x)
    best_key = None
    best_score = float("-inf")
    for (s, t), (sim_sum, _sel) in dp[node_x].items():
        score = (sim_sum * t) / float(s)
        if score > best_score:
            best_score = score
            best_key = (s, t)
    if best_key is None:
        current_subgraph_nodes = {node_x}
        current_g_value = (sim_cache[node_x] * node_dict[node_x][1]) / 1.0
        return current_subgraph_nodes, current_g_value
    current_subgraph_nodes = reconstruct(node_x, best_key)
    current_g_value = best_score
    return current_subgraph_nodes, current_g_value
def ossoss(G, node_dict, k, method="FDS", a=0.0):
    G_unused = G.copy()
    alive = {n for n in G_unused.nodes if n in node_dict}
    cache = {}
    stamp = {}
    heap = []
    def recompute_and_push(seed):
        sub_nodes, g_val = oss(G_unused, node_dict, seed, method=method, a=a)
        if sub_nodes is None:
            sub_nodes = set()
        stamp[seed] = stamp.get(seed, 0) + 1
        cache[seed] = (sub_nodes, g_val, stamp[seed])
        heapq.heappush(heap, (-g_val, seed, stamp[seed]))
    for s in list(alive):
        recompute_and_push(s)
    best_subgraphs = []
    total_g_value = 0.0
    for _ in range(k):
        sub_nodes = None
        best_g = None
        best_seed = None
        while heap:
            neg_g, s, st = heapq.heappop(heap)
            if s not in alive:
                continue
            cached = cache.get(s)
            if not cached:
                continue
            sub_nodes_c, g_c, st_c = cached
            if st_c != st:
                continue
            sub_nodes, best_g, best_seed = sub_nodes_c, -neg_g, s
            break
        if sub_nodes is None:
            break
        if best_g <= 0:
            break
        best_subgraphs.append((sub_nodes, best_g))
        total_g_value += best_g
        affected_ancestors = set()
        for n in sub_nodes:
            if G_unused.has_node(n):
                affected_ancestors |= nx.ancestors(G_unused, n)
        G_unused.remove_nodes_from(sub_nodes)
        alive = {n for n in G_unused.nodes if n in node_dict}
        for s in list(cache.keys()):
            if s not in alive:
                cache.pop(s, None)
                stamp.pop(s, None)
        for s in (affected_ancestors & alive):
            recompute_and_push(s)
    return best_subgraphs, total_g_value
def VGreedy(G, node_dict, node_x, method="FDS", a=0.0):
    try:
        descendants = nx.descendants(G, node_x)
    except Exception:
        return set(), 0.0
    target_nodes = set(descendants) | {node_x}
    ts_x = node_dict[node_x][0]
    sim_cache = {}
    for u in target_nodes:
        sim_cache[u] = 1.0 if u == node_x else sim(ts_x, node_dict[u][0], method, a=a)
    current_subgraph_nodes = {node_x}
    current_S_k = [1.0, node_dict[node_x][1], 1]
    current_g_value = current_S_k[0] * current_S_k[1] / current_S_k[2]
    step = 0
    while True:
        step += 1
        frontier = set()
        for u in current_subgraph_nodes:
            for v in G.successors(u):
                if v in target_nodes and v not in current_subgraph_nodes:
                    frontier.add(v)
        if not frontier:
            break
        best_new_g_value = float("-inf")
        best_new_S_k = None
        best_choice = None
        for n in frontier:
            sim_val = sim_cache[n]
            value = node_dict[n][1]
            new_S_k = [
                current_S_k[0] + sim_val,
                current_S_k[1] + value,
                current_S_k[2] + 1
            ]
            new_g_value = new_S_k[0] * new_S_k[1] / new_S_k[2]
            if new_g_value > best_new_g_value:
                best_new_g_value = new_g_value
                best_new_S_k = new_S_k
                best_choice = n
        if best_choice is None or best_new_g_value <= current_g_value:
            break
        current_subgraph_nodes.add(best_choice)
        current_S_k = best_new_S_k
        current_g_value = best_new_g_value
    return current_subgraph_nodes, current_g_value
def VVGreedy(G, node_dict, k, method="FDS", a=0.0):
    used_nodes = set()
    best_subgraphs = []
    total_g_value = 0
    G_unused = G.copy()
    for i in range(k):
        best_subgraph = None
        best_g_value = -float('inf')
        total_candidates = 0
        for j, node in enumerate(node_dict):
            if node in used_nodes:
                continue
            total_candidates += 1
            sub_nodes, g_value = VGreedy(
                G_unused, node_dict, node, method=method, a=a
            )
            if g_value > best_g_value:
                best_g_value = g_value
                best_subgraph = sub_nodes
        if best_subgraph is None:
            break
        best_subgraphs.append((best_subgraph, best_g_value))
        used_nodes.update(best_subgraph)
        total_g_value += best_g_value
        for n in best_subgraph:
            if G_unused.has_node(n):
                G_unused.remove_edges_from(list(G_unused.in_edges(n)) + list(G_unused.out_edges(n)))
    return best_subgraphs, total_g_value

def PGreedy(G, node_dict, node_x, method="FDS", a=0.0):
    EPS = 1e-12
    try:
        descendants = nx.descendants(G, node_x)
    except Exception:
        return set(), 0.0
    target_nodes = set(descendants) | {node_x}
    ts_x = node_dict[node_x][0]
    sim_cache = {
        n: (
            1.0
            if n == node_x
            else sim(ts_x, node_dict[n][0], method, a=a)
        )
        for n in target_nodes
    }
    parent_map = {}
    for n in descendants:
        p = next(iter(G.predecessors(n)), None)
        if p is not None:
            parent_map[n] = p
    prefix = {node_x: (0.0, 0.0, 0)}
    def ensure_prefix(n):
        if n in prefix:
            return prefix[n]
        p = parent_map.get(n, None)
        own_v = float(node_dict[n][1])
        own_s = float(sim_cache.get(n, 0.0))
        own_c = 1
        if p is not None:
            pv, ps, pc = ensure_prefix(p)
            prefix[n] = (pv + own_v, ps + own_s, pc + own_c)
        else:
            prefix[n] = (own_v, own_s, own_c)
        return prefix[n]
    for n in descendants:
        ensure_prefix(n)
    current_subgraph_nodes = {node_x}
    current_S_k = [1.0, float(node_dict[node_x][1]), 1]  # similarity, value, count
    current_g_value = current_S_k[0] * current_S_k[1] / current_S_k[2]
    def nearest_boundary(u):
        x = u
        while x not in current_subgraph_nodes:
            x = parent_map.get(x, None)
            if x is None:
                return node_x
        return x
    while True:
        candidates = [n for n in descendants if n not in current_subgraph_nodes]
        if not candidates:
            break
        best_u = None
        best_S_k = None
        best_g = float("-inf")
        for n in candidates:
            b = nearest_boundary(n)
            nv, ns, nc = prefix[n]
            bv, bs, bc = prefix.get(b, (0.0, 0.0, 0))
            dv, ds, dc = (nv - bv, ns - bs, nc - bc)
            if dc <= 0:
                continue
            new_S_k = (current_S_k[0] + ds, current_S_k[1] + dv, current_S_k[2] + dc)
            new_g = new_S_k[0] * new_S_k[1] / new_S_k[2]
            if (new_g > best_g + EPS) or (
                abs(new_g - best_g) <= EPS and best_u is not None and type(n) == type(best_u) and n < best_u
            ):
                best_u = n
                best_S_k = new_S_k
                best_g = new_g
            elif best_u is None:
                best_u = n
                best_S_k = new_S_k
                best_g = new_g
        if (best_u is None) or (best_g <= current_g_value + EPS):
            break
        add_nodes = set()
        u = best_u
        b = nearest_boundary(u)
        while u != b and u not in current_subgraph_nodes:
            add_nodes.add(u)
            u = parent_map.get(u, b)
        current_subgraph_nodes.update(add_nodes)
        current_S_k = [best_S_k[0], best_S_k[1], best_S_k[2]]
        current_g_value = best_g
    return current_subgraph_nodes, current_g_value
def PPGreedy(G, node_dict, k, method="FDS", a=0.0):
    used_nodes = set()
    best_subgraphs = []
    total_g_value = 0.0
    G_unused = G.copy()
    cache = {}
    affected_seeds_next = None
    for _round in range(k):
        best_subgraph = None
        best_g_value = float('-inf')
        for seed in list(G_unused.nodes):
            if seed not in node_dict:
                continue
            if seed in used_nodes:
                continue
            need_recompute = False
            if seed not in cache:
                need_recompute = True
            elif affected_seeds_next is not None and seed in affected_seeds_next:
                need_recompute = True
            if need_recompute:
                sub_nodes, g_value = PGreedy(
                    G_unused, node_dict, seed, method=method, a=a
                )
                cache[seed] = (sub_nodes, g_value)
            else:
                sub_nodes, g_value = cache[seed]
            if g_value > best_g_value:
                best_g_value = g_value
                best_subgraph = sub_nodes
        if not best_subgraph:
            break
        if best_g_value <= 0:
            break
        best_subgraphs.append((best_subgraph, best_g_value))
        total_g_value += best_g_value
        used_nodes.update(best_subgraph)
        affected_ancestors = set()
        for n in best_subgraph:
            if G_unused.has_node(n):
                affected_ancestors |= nx.ancestors(G_unused, n)
        G_unused.remove_nodes_from(best_subgraph)
        for s in list(cache.keys()):
            if s in best_subgraph or not G_unused.has_node(s):
                cache.pop(s, None)
        affected_seeds_next = {s for s in affected_ancestors if G_unused.has_node(s)}
    return best_subgraphs, total_g_value

def _to_np_1d(ts):
    x = np.asarray(ts, dtype=np.float64).ravel()
    if x.ndim != 1:
        raise ValueError("ts must be 1-D.")
    return x
def _align_truncate(x, y):
    n = min(len(x), len(y))
    return x[:n], y[:n]
def _z_norm(x):
    mu = np.mean(x)
    sigma = np.std(x)
    if sigma == 0:
        return np.zeros_like(x)
    return (x - mu) / sigma
def _sim_from_distance(d, scale=None):
    if d < 0:
        raise ValueError("distance must be non-negative.")
    if scale is None or scale <= 0:
        scale = 1.0
    return 1.0 - 2.0 * (d / (d + scale))
def _alpha_skew(s, alpha=0.0):
    s = float(np.clip(s, -1.0, 1.0))
    denom = 1.0 - alpha * s
    if abs(denom) < 1e-12:
        return np.sign(s)
    return (s - alpha) / denom
def _fds_similarity(ts1, ts2, alpha=0.0):
    f = _to_np_1d(ts1)
    g = _to_np_1d(ts2)
    f, g = _align_truncate(f, g)
    if len(f) == 0:
        return 0.0
    f = f - np.mean(f)
    g = g - np.mean(g)

    F = np.abs(np.fft.fft(f))[: len(f) // 2]
    G = np.abs(np.fft.fft(g))[: len(g) // 2]

    nF = np.linalg.norm(F)
    nG = np.linalg.norm(G)
    if nF == 0 or nG == 0:
        s = 0.0
    else:
        s = float(np.dot(F / nF, G / nG))  # bounded to [-1, 1]
    return _alpha_skew(s, alpha)
def _euclidean_similarity(ts1, ts2, scale=None, alpha=0.0):
    x = _to_np_1d(ts1)
    y = _to_np_1d(ts2)
    x, y = _align_truncate(x, y)
    d = np.linalg.norm(x - y)
    if scale is None:
        scale = np.sqrt(len(x)) * np.std(np.concatenate([x, y])) if len(x) else 1.0
        if scale <= 0:
            return 1.0
    s = _sim_from_distance(d, scale)
    return _alpha_skew(s, alpha)
def _znorm_euclidean_similarity(ts1, ts2, alpha=0.0):
    x = _to_np_1d(ts1)
    y = _to_np_1d(ts2)
    x, y = _align_truncate(x, y)
    xz = _z_norm(x)
    yz = _z_norm(y)
    d2 = np.sum((xz - yz) ** 2)
    n = len(xz)
    if n == 0:
        return 0.0
    corr = 1.0 - d2 / (2.0 * n)
    corr = float(np.clip(corr, -1.0, 1.0))
    return _alpha_skew(corr, alpha)
def _dtw_distance(ts1, ts2, window=None):
    x = _to_np_1d(ts1)
    y = _to_np_1d(ts2)
    n, m = len(x), len(y)
    if n == 0 or m == 0:
        return float(abs(n - m))
    if window is None:
        window = max(n, m)
    window = max(window, abs(n - m))
    INF = 1e100
    dtw = np.full((n + 1, m + 1), INF, dtype=np.float64)
    dtw[0, 0] = 0.0
    for i in range(1, n + 1):
        j_start = max(1, i - window)
        j_end = min(m, i + window)
        for j in range(j_start, j_end + 1):
            cost = abs(x[i - 1] - y[j - 1])
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])
    return float(dtw[n, m])
def _dtw_similarity(ts1, ts2, window=None, scale=None, alpha=0.0):
    d = _dtw_distance(ts1, ts2, window=window)
    if scale is None:
        x = _to_np_1d(ts1)
        y = _to_np_1d(ts2)
        L = 0.5 * (len(x) + len(y))
        std = np.std(np.concatenate([x, y])) if (len(x) and len(y)) else 1.0
        scale = np.sqrt(max(L, 1.0)) * max(std, 1e-12)
    s = _sim_from_distance(d, scale)
    return _alpha_skew(s, alpha)
def _lcss_similarity(ts1, ts2, eps=None, delta=5, alpha=0.0):
    x = _to_np_1d(ts1)
    y = _to_np_1d(ts2)
    n, m = len(x), len(y)
    if n == 0 or m == 0:
        return -1.0
    if eps is None:
        eps = 0.5 * np.std(np.concatenate([x, y])) if (n and m) else 0.0
    L = np.zeros((n + 1, m + 1), dtype=np.int32)
    for i in range(1, n + 1):
        j_start = max(1, i - delta)
        j_end = min(m, i + delta)
        for j in range(j_start, j_end + 1):
            if abs(x[i - 1] - y[j - 1]) <= eps:
                L[i, j] = L[i - 1, j - 1] + 1
            else:
                L[i, j] = max(L[i - 1, j], L[i, j - 1])
    lcss_len = int(L[n, m])
    s = 2.0 * (lcss_len / float(min(n, m))) - 1.0
    return _alpha_skew(float(s), alpha)
def _msm_distance(ts1, ts2, c=1.0):
    x = _to_np_1d(ts1)
    y = _to_np_1d(ts2)
    n, m = len(x), len(y)
    if n == 0:
        return float(m * c)
    if m == 0:
        return float(n * c)
    def C(a, b, cst):
        if (b <= a <= cst) or (cst <= a <= b):
            return c
        else:
            return c + min(abs(a - b), abs(a - cst))
    D = np.zeros((n + 1, m + 1), dtype=np.float64)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        prev_x = x[i - 2] if i > 1 else x[i - 1]
        D[i, 0] = D[i - 1, 0] + C(x[i - 1], prev_x, prev_x)
    for j in range(1, m + 1):
        prev_y = y[j - 2] if j > 1 else y[j - 1]
        D[0, j] = D[0, j - 1] + C(y[j - 1], prev_y, prev_y)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost1 = D[i - 1, j - 1] + abs(x[i - 1] - y[j - 1])
            prev_x = x[i - 2] if i > 1 else x[i - 1]
            cost2 = D[i - 1, j] + C(x[i - 1], prev_x, y[j - 1])
            prev_y = y[j - 2] if j > 1 else y[j - 1]
            cost3 = D[i, j - 1] + C(y[j - 1], x[i - 1], prev_y)
            D[i, j] = min(cost1, cost2, cost3)
    return float(D[n, m])
def _msm_similarity(ts1, ts2, c=1.0, scale=None, alpha=0.0):
    d = _msm_distance(ts1, ts2, c=c)
    if scale is None:
        x = _to_np_1d(ts1)
        y = _to_np_1d(ts2)
        L = 0.5 * (len(x) + len(y))
        std = np.std(np.concatenate([x, y])) if (len(x) and len(y)) else 1.0
        scale = np.sqrt(max(L, 1.0)) * max(std, 1e-12)
    s = _sim_from_distance(d, scale)
    return _alpha_skew(s, alpha)
_SIMILARITY_METHOD_ALIASES = {
    "fds": "FDS",
    "euclid": "Euclidean",
    "euclidean": "Euclidean",
    "znorm-euclidean": "znorm_euclidean",
    "znorm_euclidean": "znorm_euclidean",
    "z-euclid": "znorm_euclidean",
    "z-normalized-euclidean": "znorm_euclidean",
    "dtw": "DTW",
    "lcss": "LCSS",
    "msm": "MSM",
}


def normalize_similarity_method(method):
    if not isinstance(method, str) or not method.strip():
        raise ValueError("`method` is required and must be a non-empty string.")
    key = method.strip().lower().replace(" ", "-")
    try:
        return _SIMILARITY_METHOD_ALIASES[key]
    except KeyError as exc:
        supported = "FDS, Euclidean, Z-Normalized Euclidean, DTW, LCSS, MSM"
        raise ValueError(
            f"Unknown similarity method: {method}. Supported methods: {supported}."
        ) from exc


def sim(ts1, ts2, method, **kwargs):
    alpha = kwargs.pop("alpha", kwargs.pop("a", 0.0))
    _ = kwargs.get("k", None)
    m = normalize_similarity_method(method).lower()
    if m == "fds":
        return _fds_similarity(ts1, ts2, alpha=alpha)
    elif m == "euclidean":
        return _euclidean_similarity(ts1, ts2, alpha=alpha, **kwargs)
    elif m == "znorm_euclidean":
        return _znorm_euclidean_similarity(ts1, ts2, alpha=alpha)
    elif m == "dtw":
        return _dtw_similarity(ts1, ts2, alpha=alpha, **kwargs)
    elif m == "lcss":
        return _lcss_similarity(ts1, ts2, alpha=alpha, **kwargs)
    elif m == "msm":
        return _msm_similarity(ts1, ts2, alpha=alpha, **kwargs)
    else:
        raise ValueError(f"Unknown method: {method}")


_STRATEGY_ALIASES = {
    "path-greedy": "Path-greedy",
    "p-greedy": "Path-greedy",
    "pgreedy": "Path-greedy",
    "optimal-search": "Optimal-Search",
    "optimal-subtree-search": "Optimal-Search",
    "oss": "Optimal-Search",
    "v-greedy": "V-greedy",
    "vertex-greedy": "V-greedy",
    "vgreedy": "V-greedy",
}

MAX_OPTIMAL_SEARCH_NODES = 50


class OptimalSearchLimitError(ValueError):
    """Raised when exact search exceeds a deployment resource guard."""


def optimal_search_node_limit():
    """Return the configurable exact-search guard; zero disables the guard."""
    raw_limit = os.environ.get(
        "HTSA_OPTIMAL_MAX_NODES", str(MAX_OPTIMAL_SEARCH_NODES)
    )
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("HTSA_OPTIMAL_MAX_NODES must be an integer") from exc
    return None if limit <= 0 else limit


def normalize_optimal_overflow_policy(policy):
    """Normalize behavior when exact search exceeds its resource guard."""
    key = str(policy or "path-greedy").strip().lower().replace("_", "-")
    aliases = {
        "fallback": "path-greedy",
        "path-greedy": "path-greedy",
        "error": "error",
        "reject": "error",
    }
    try:
        return aliases[key]
    except KeyError as exc:
        raise ValueError(
            "optimal_overflow must be `path-greedy` or `error`"
        ) from exc


def normalize_strategy(strategy):
    if not isinstance(strategy, str) or not strategy.strip():
        raise ValueError("`strategy` is required and must be a non-empty string.")
    key = strategy.strip().lower().replace("_", "-").replace(" ", "-")
    try:
        return _STRATEGY_ALIASES[key]
    except KeyError as exc:
        supported = "Path-greedy, Optimal-Search, V-greedy"
        raise ValueError(
            f"Unknown strategy: {strategy}. Supported strategies: {supported}."
        ) from exc


def normalize_hierarchy_to_forest(G, node_dict, method="FDS", a=0.0):
    """Resolve multiple parents using temporal affinity and stable tie-breaking."""
    forest = G.copy()
    dropped_edges = []
    for node in sorted(forest.nodes, key=str):
        parents = sorted(forest.predecessors(node), key=str)
        if len(parents) <= 1:
            continue
        scored_parents = []
        for parent in parents:
            score = float("-inf")
            if node in node_dict and parent in node_dict:
                score = sim(
                    node_dict[node][0], node_dict[parent][0], method, a=a
                )
            scored_parents.append((score, parent))
        scored_parents.sort(key=lambda item: (-item[0], str(item[1])))
        keep_parent = scored_parents[0][1]
        for parent in parents:
            if parent != keep_parent:
                forest.remove_edge(parent, node)
                dropped_edges.append((parent, node))
    return forest, dropped_edges


def run_htsa_strategy(
    G,
    node_dict,
    k,
    strategy,
    method="FDS",
    a=0.0,
    optimal_overflow="error",
):
    """Run a strategy and return transparent resource-guard metadata."""
    requested_strategy = normalize_strategy(strategy)
    canonical_strategy = requested_strategy
    canonical_method = normalize_similarity_method(method)
    overflow_policy = normalize_optimal_overflow_policy(optimal_overflow)
    node_limit = optimal_search_node_limit()
    execution = {
        "requested_strategy": requested_strategy,
        "executed_strategy": requested_strategy,
        "fallback_applied": False,
        "optimal_search_node_limit": node_limit,
        "optimal_overflow_policy": overflow_policy,
        "reason": None,
    }
    if (
        requested_strategy == "Optimal-Search"
        and node_limit is not None
        and G.number_of_nodes() > node_limit
    ):
        reason = (
            f"Optimal-Search received {G.number_of_nodes()} nodes, exceeding "
            f"the configured interactive guard of {node_limit}."
        )
        if overflow_policy == "error":
            raise OptimalSearchLimitError(
                reason
                + " Set HTSA_OPTIMAL_MAX_NODES=0 for an unguarded controlled "
                "run, raise the limit, or use Path-greedy."
            )
        canonical_strategy = "Path-greedy"
        execution.update({
            "executed_strategy": canonical_strategy,
            "fallback_applied": True,
            "reason": reason,
        })
    dispatch = {
        "Path-greedy": PPGreedy,
        "Optimal-Search": ossoss,
        "V-greedy": VVGreedy,
    }
    result = dispatch[canonical_strategy](
        G, node_dict, k, method=canonical_method, a=a
    )
    return canonical_strategy, canonical_method, result, execution

def build_summary_tree(edges, groups_tuple):
    parent, children, nodes = {}, {}, set()
    for p, c in edges:
        nodes.add(p); nodes.add(c)
        parent[c] = p
        children.setdefault(p, []).append(c)
        children.setdefault(c, [])
    roots = sorted((u for u in nodes if u not in parent), key=str)
    if len(roots) == 0:
        raise ValueError("no root")
    if len(roots) == 1:
        R = roots[0]
    else:
        VR = 'unimportant'
        children.setdefault(VR, [])
        for r in roots:
            parent[r] = VR
            children[VR].append(r)
        nodes.add(VR)
        R = VR
    from collections import deque, defaultdict
    depth = {R: 0}
    dq = deque([R])
    while dq:
        u = dq.popleft()
        for v in children.get(u, []):
            depth[v] = depth[u] + 1
            dq.append(v)
    groups_list = groups_tuple[0] if groups_tuple else []
    group_nodes_list = []
    for g in groups_list:
        if isinstance(g, (list, tuple)) and g:
            lst = g[0]
            if isinstance(lst, (set, list, tuple)):
                group_nodes_list.append(sorted(lst, key=str))
    node_to_group_idx = {}
    for idx, lst in enumerate(group_nodes_list):
        for u in lst:
            node_to_group_idx[u] = idx
    group_root = {}
    for idx, lst in enumerate(group_nodes_list):
        s = set(lst)
        candidates = [u for u in lst if parent.get(u) not in s]
        if not candidates:
            raise ValueError(f"group {idx} no root")
        candidates.sort(key=lambda x: (depth.get(x, float('inf')), str(x)))
        group_root[idx] = candidates[0]
    def climb_to_group_root(u):
        v = parent.get(u, None)
        first_step = True
        while v is not None:
            if v in node_to_group_idx:
                b_idx = node_to_group_idx[v]
                B = group_root[b_idx]
                return B, first_step
            v = parent.get(v, None)
            first_step = False
        return None, False
    gprime_edges, added_edges, added_nodes = [], set(), set()
    def add_edge(p, c):
        if (p, c) not in added_edges:
            added_edges.add((p, c))
            gprime_edges.append((p, c))
            added_nodes.add(p); added_nodes.add(c)
    def S(u):
        return f"S_{u}"
    if R in node_to_group_idx:
        gprime_root = S(R)
    else:
        gprime_root = "unimportant0"
    added_nodes.add(gprime_root)
    for idx, A in group_root.items():
        if A == R: continue
        B, is_immediate = climb_to_group_root(A)
        if B is not None:
            if is_immediate:
                add_edge(S(B), S(A))
            else:
                ua = f"unimportant_{A}"
                add_edge(S(B), ua); add_edge(ua, S(A))
        else:
            ua = f"unimportant_{A}"
            add_edge(gprime_root, ua); add_edge(ua, S(A))
    def is_unimportant(x): return isinstance(x, str) and x.startswith("unimportant")
    adj, indeg, par = defaultdict(list), defaultdict(int), {}
    for p, c in gprime_edges:
        adj[p].append(c); indeg[c] += 1; par[c] = p
    changed = True
    while changed:
        changed = False
        pairs = [(p, c) for p in list(adj.keys()) for c in list(adj[p]) if is_unimportant(p) and is_unimportant(c)]
        if not pairs: break
        for u1, u2 in pairs:
            if u2 not in adj[u1]: continue
            adj[u1].remove(u2); indeg[u2] -= 1
            if par.get(u2) == u1: del par[u2]
            for w in list(adj[u2]):
                adj[u2].remove(w); indeg[w] -= 1
                if w not in adj[u1]:
                    adj[u1].append(w); indeg[w] += 1
                par[w] = u1
            if not adj[u2] and indeg[u2] <= 0:
                del adj[u2]
            if gprime_root == u2:
                gprime_root = u1
            changed = True
    new_edges, seen = [], set()
    for p, lst in adj.items():
        for c in lst:
            if (p, c) not in seen:
                seen.add((p, c)); new_edges.append((p, c))
    gprime_edges = new_edges
    # Return group roots in the same order as the selected groups.
    group_root_list = [group_root[i] for i in sorted(group_root.keys())]
    return gprime_edges, gprime_root, group_root_list

# Flask application and writable runtime directories.
APP_VERSION = "0.2.0"
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = int(
    os.environ.get("HTSA_MAX_REQUEST_BYTES", str(64 * 1024 * 1024))
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "datasets")
DATA_DIR = os.environ.get(
    "HTSA_RUNTIME_DIR", os.path.join(BASE_DIR, "runtime_data")
)
os.makedirs(DATA_DIR, exist_ok=True)
TUPIAN_DIR = os.path.join(BASE_DIR, "exports")
os.makedirs(TUPIAN_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)


def _safe_filename(name: str) -> str:
    """Return a filesystem-safe filename fragment."""
    if not isinstance(name, str) or not name:
        return "unnamed"
    name = name.strip()
    name = re.sub(r"[^\w\.-]", "_", name, flags=re.UNICODE)
    return name[:120] or "unnamed"


def _save_record_to_file(
    record: dict, original_filename: str, run_id: str | None = None
) -> str:
    """Persist a run record as JSON and return its path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = _safe_filename(original_filename or "unnamed")
    uid = (run_id or uuid.uuid4().hex)[:12]
    save_path = os.path.join(DATA_DIR, f"{ts}_{safe_name}_{uid}.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return save_path



@app.route("/", methods=["GET"])
def home():
    return render_template("appnewest.html")


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({
        "ok": True,
        "service": "HTSA-Explorer",
        "version": APP_VERSION,
        "capabilities": {
            "browser_history": "IndexedDB",
            "server_audit_records": True,
            "optimal_search_node_limit": optimal_search_node_limit(),
            "optimal_search_overflow": "path-greedy",
        },
    })


@app.after_request
def add_security_headers(response):
    """Set low-risk baseline headers for local and hosted deployments."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


PRESET_DATASETS = {
    "acm": "acm.graphml",
    "stockgraph": "equities.graphml",
    "regional-gdp": "european_regional_gdp.graphml",
}

@app.route("/api/dataset/<name>", methods=["GET"])
def api_dataset(name):
    filename = PRESET_DATASETS.get(name)
    if not filename:
        return jsonify({"ok": False, "error": "dataset not found"}), 404
    filepath = os.path.join(DATASET_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"ok": False, "error": "file missing on server"}), 404
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return Response(content, mimetype="application/xml", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'
    })


@app.route("/api/save_svgs", methods=["POST"])
def api_save_svgs():
    try:
        data = request.get_json(silent=True, force=True) or {}
        folder = _safe_filename((data.get("folder") or "unnamed").replace(".graphml", ""))
        files = data.get("files") or []

        if not isinstance(files, list):
            return jsonify({"ok": False, "error": "files must be a list"}), 400

        target_dir = os.path.join(TUPIAN_DIR, folder)
        os.makedirs(target_dir, exist_ok=True)

        saved = []
        skipped = []
        for i, item in enumerate(files, 1):
            if not isinstance(item, dict):
                skipped.append({"index": i, "reason": "invalid file payload"})
                continue

            name = _safe_filename(item.get("name") or f"image_{i}.svg")
            if not name.lower().endswith(".svg"):
                name += ".svg"
            svg = item.get("svg")
            if not isinstance(svg, str) or "<svg" not in svg:
                skipped.append({"name": name, "reason": "invalid svg content"})
                continue

            path = os.path.join(target_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)
            saved.append(path)

        return jsonify({
            "ok": True,
            "folder": target_dir,
            "saved_count": len(saved),
            "saved_files": saved,
            "skipped": skipped
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500




@app.route("/api/htsa", methods=["POST"])
def api_htsa():
    request_started = time.perf_counter()
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = uuid.uuid4().hex
    try:
        # Parse and validate the request before running an algorithm.
        data = request.get_json(silent=True, force=True)
        if not data:
            logger.warning("Received empty JSON for /api/htsa")
            return jsonify({"ok": False, "error": "Empty JSON"}), 400

        filename = data.get("filename") or "unnamed"
        ts_key = data.get("ts_key") or "time_series"
        htsa_options = data.get("htsa") or {}
        method = htsa_options.get("method", "FDS")
        strategy = htsa_options.get("strategy", "Path-greedy")
        optimal_overflow = htsa_options.get(
            "optimal_overflow", "path-greedy"
        )
        try:
            a = float(htsa_options.get("a", 0))
            if not np.isfinite(a):
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "a must be a finite number"}), 400
        try:
            raw_k = float(htsa_options.get("k", 1))
            if not np.isfinite(raw_k) or not raw_k.is_integer() or raw_k < 1:
                raise ValueError
            k = int(raw_k)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "k must be a positive integer"}), 400
        try:
            strategy = normalize_strategy(strategy)
            method = normalize_similarity_method(method)
            optimal_overflow = normalize_optimal_overflow_policy(
                optimal_overflow
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        raw_node_dict = data.get("node_dict") or {}
        edges = data.get("edges") or []
        raw_G = data.get("G")

        logger.info(
            "HTSA request filename=%s strategy=%s method=%s a=%s k=%s",
            filename,
            strategy,
            method,
            a,
            k,
        )

        # Normalize the browser node_dict representation.
        node_dict = {}
        for nid, payload in raw_node_dict.items():
            ts = None
            val = None
            info = {}
            if isinstance(payload, dict):
                ts = payload.get(ts_key) or payload.get("time_series")
                val = payload.get("value")
                info = payload.get("info") or {}
            elif isinstance(payload, (list, tuple)):
                ts = payload[0] if len(payload) > 0 else None
                val = payload[1] if len(payload) > 1 else None
                if len(payload) > 2 and isinstance(payload[2], dict):
                    info = payload[2]
            if ts is None:
                continue
            if val is None:
                try:
                    val = float(np.nansum(ts))
                except Exception:
                    val = 0.0
            try:
                val = float(val)
                if not np.isfinite(val):
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify({
                    "ok": False,
                    "error": f"Node {nid!r} has a non-numeric value"
                }), 400
            node_dict[nid] = (ts, val, info)

        if not node_dict:
            return jsonify({"ok": False, "error": "No valid nodes parsed"}), 400

        G = to_nx_graph(raw_G, node_dict=node_dict)
        # Merge the explicit edge list as a fallback when G is absent or sparse.
        G.add_nodes_from(node_dict.keys())
        for u, v in edges:
            if u in node_dict and v in node_dict:
                G.add_edge(u, v)

        # Restrict computation to nodes with usable time series. The
        # summarizers assume a directed forest: acyclic and at most one parent.
        G = G.subgraph(node_dict.keys()).copy()
        if G.number_of_nodes() == 0:
            return jsonify({"ok": False, "error": "No valid graph nodes"}), 400
        if not nx.is_directed_acyclic_graph(G):
            return jsonify({"ok": False, "error": "The hierarchy must be acyclic"}), 400
        G, dropped_parent_edges = normalize_hierarchy_to_forest(
            G, node_dict, method=method, a=a
        )
        valid_edges = list(G.edges())

        normalized_input = {
            "ts_key": ts_key,
            "nodes": [
                [str(node), node_dict[node][0], node_dict[node][1], node_dict[node][2]]
                for node in sorted(G.nodes(), key=str)
            ],
            "edges": [
                [str(parent), str(child)]
                for parent, child in sorted(valid_edges, key=lambda edge: (str(edge[0]), str(edge[1])))
            ],
        }
        analysis_input_sha256 = hashlib.sha256(
            json.dumps(
                normalized_input,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest().upper()

        try:
            strategy, method, jieguo, execution = run_htsa_strategy(
                G,
                node_dict,
                k,
                strategy=strategy,
                method=method,
                a=a,
                optimal_overflow=optimal_overflow,
            )
        except OptimalSearchLimitError as exc:
            return jsonify({
                "ok": False,
                "error": str(exc),
                "error_code": "optimal_search_resource_guard",
                "optimal_search_node_limit": optimal_search_node_limit(),
            }), 422
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        best_subgraphs, total_g_value = jieguo

        # KDD Equation (7) defines coverage as the total importance mass of
        # vertices contained in the selected subtrees.  Keep the raw mass and
        # expose a normalized fraction for cross-dataset display; node-count
        # compression is a separate diagnostic.
        selected_nodes = set()
        for sg_nodes, _ in best_subgraphs:
            selected_nodes.update(sg_nodes)
        selected_importance = float(sum(
            node_dict[node][1] for node in selected_nodes
        ))
        total_importance = float(sum(
            node_dict[node][1] for node in G.nodes()
        ))
        importance_fraction = (
            selected_importance / total_importance
            if total_importance > 0 else None
        )

        # Build the summary tree and recover the selected group roots.
        gprime_edges, gprime_root, group_root_list = build_summary_tree(
            valid_edges, jieguo
        )

        logger.info(
            "Summary tree built with root=%s and %d edges",
            gprime_root,
            len(gprime_edges),
        )
        # Persist summary edges for auditing and reproducibility.
        txt_filename = (
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_"
            f"{_safe_filename(filename)}_{run_id[:12]}_summary_edges.txt"
        )
        summary_path = os.path.join(DATA_DIR, txt_filename)
        with open(summary_path, "w", encoding="utf-8") as file:
            for edge in gprime_edges:
                file.write(str(edge) + "\n")

        # Return a deterministic JSON representation for browser and API clients.
        subgraph_payload = []
        for i, (sg_nodes, g_val) in enumerate(best_subgraphs):
            root_name = group_root_list[i] if i < len(group_root_list) else None
            subgraph_payload.append({
                "root": root_name,
                "nodes": [str(x) for x in sorted(sg_nodes, key=str)],
                "g_value": g_val
            })

        response_payload = {
            "ok": True,
            "gprime_edges": gprime_edges,
            "gprime_root": gprime_root,
            "summary_file": txt_filename,
            "subgraphs": subgraph_payload,
            "group_roots": group_root_list,
            "analysis_graph": {
                "vertices": G.number_of_nodes(),
                "edges": G.number_of_edges()
            },
            "coverage": {
                "definition": "selected importance / total importance",
                "selected_importance": selected_importance,
                "total_importance": total_importance,
                "importance_fraction": importance_fraction,
                "selected_vertices": len(selected_nodes),
                "total_vertices": G.number_of_nodes()
            },
            "strategy": strategy,
            "requested_strategy": execution["requested_strategy"],
            "execution": execution,
            "method": method,
            "a": a,
            "k": k,
            "ts_key": ts_key,
            "normalization": {
                "dropped_multi_parent_edge_count": len(dropped_parent_edges),
                "dropped_multi_parent_edges": [
                    [str(parent), str(child)]
                    for parent, child in dropped_parent_edges
                ]
            },
            "audit": {
                "run_id": run_id,
                "created_at": created_at,
                "software_version": APP_VERSION,
                "analysis_input_sha256": analysis_input_sha256,
                "runtime_seconds": time.perf_counter() - request_started,
            },
        }

        audit_record = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": created_at,
            "software_version": APP_VERSION,
            "request": {
                "filename": filename,
                "size": data.get("size"),
                "ts_key": ts_key,
                "htsa": {
                    "method": method,
                    "requested_strategy": execution["requested_strategy"],
                    "executed_strategy": execution["executed_strategy"],
                    "optimal_overflow": optimal_overflow,
                    "a": a,
                    "k": k,
                },
                "analysis_input_sha256": analysis_input_sha256,
            },
            "response": response_payload,
        }
        record_path = _save_record_to_file(
            audit_record, filename, run_id=run_id
        )
        response_payload["audit"]["record_file"] = os.path.basename(record_path)
        return jsonify(response_payload)


    except Exception as e:
        logger.exception("Error during HTSA processing")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    debug = os.environ.get("HTSA_DEBUG", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    host = os.environ.get("HTSA_HOST", "127.0.0.1")
    port = int(os.environ.get("HTSA_PORT", "5000"))
    app.run(host=host, port=port, debug=debug)
