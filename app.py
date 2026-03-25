# -*- coding: utf-8 -*-
"""
Created on Sun Oct 26 20:32:22 2025

后端：Flask
功能：
  - GET  /                -> 渲染 templates/appnewest.html
  - POST /submit          -> 表单示例（保持你原逻辑）
  - POST /api/htsa        -> 接收前端 JSON，保存 node_dict / edges / G / method / strategy / a / k / ts_key
  - POST /api/sim         -> 计算两条时间序列的相似度，使用你提供的 sim()（method 由输入决定）
"""

import os
import re
import json
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for,session
import logging
#from utils import ossoss, VVGreedy, PPGreedy, sim, build_summary_tree

import heapq
import networkx as nx
import numpy as np
def to_nx_graph(G_in, node_dict=None):
    """把前端传来的 G 统一转成 networkx.DiGraph。"""
    print(f"[to_nx_graph] Received G_in: {G_in}")  # 打印输入 G_in 的类型和内容

    if isinstance(G_in, (nx.DiGraph, nx.Graph)):
        print(f"[to_nx_graph] G_in is already a networkx graph, copying.")  # 如果 G_in 是一个 networkx 图
        return G_in.copy()

    G = nx.DiGraph()

    # 如果 G_in 不是字典类型，直接通过 node_dict 和 edges 重建图
    if not isinstance(G_in, dict):
        print("[to_nx_graph] G_in is not a dict, using node_dict to create graph.")  # 打印警告
        if node_dict:
            G.add_nodes_from(node_dict.keys())  # 通过 node_dict 添加节点
        return G

    nodes = G_in.get('nodes', [])
    edges = G_in.get('edges', [])

    # 打印节点和边的类型
    print(f"[to_nx_graph] Nodes: {nodes}")
    print(f"[to_nx_graph] Edges: {edges}")

    # 检查 nodes 是否是字典
    if isinstance(nodes, dict):
        print(f"[to_nx_graph] Nodes is a dictionary.")  # 打印 nodes 的类型
        for nid, attrs in nodes.items():
            print(f"[to_nx_graph] Processing node {nid} with attributes {attrs}")  # 打印每个节点的属性

            # 如有 node_dict，合并常用属性（可选）
            if node_dict and nid in node_dict:
                ts, val, info = node_dict[nid]
                attrs = {**(attrs or {}), 'time_series': ts, 'value': val, **(info or {})}
                print(f"[to_nx_graph] Node {nid} updated with node_dict data: {attrs}")  # 打印更新后的节点信息

            G.add_node(nid, **(attrs or {}))  # 添加节点到图

    elif isinstance(nodes, list):
        print("[to_nx_graph] Nodes is a list.")  # 打印 nodes 是列表的情况
        for nid in nodes:
            attrs = {}
            if node_dict and nid in node_dict:
                ts, val, info = node_dict[nid]
                attrs = {'time_series': ts, 'value': val, **(info or {})}
                print(f"[to_nx_graph] Node {nid} added with attributes from node_dict: {attrs}")  # 打印添加的节点信息
            G.add_node(nid, **attrs)  # 添加节点到图

    else:
        print(f"[graph] unexpected nodes type: {type(nodes)}")  # 打印类型错误的情况

    # 检查 edges 是否是列表
    if isinstance(edges, list):
        print(f"[to_nx_graph] Edges is a list.")  # 打印 edges 是列表的情况
        for e in edges:
            if isinstance(e, (list, tuple)) and len(e) >= 2:
                print(f"[to_nx_graph] Adding edge {e}")  # 打印每个添加的边
                G.add_edge(e[0], e[1])  # 添加边到图
            else:
                print(f"[graph] unexpected edge format: {e}")  # 打印边的格式错误
    else:
        print(f"[graph] unexpected edges type: {type(edges)}")  # 打印边类型错误的情况

    return G



def oss(G, node_dict, node_x):
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
            sim_cache[other_node] = sim(ts_x, ts_other)
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
def ossoss(G, node_dict, k):
    G_unused = G.copy()
    alive = {n for n in G_unused.nodes if n in node_dict}
    cache = {}
    stamp = {}
    heap = []
    def recompute_and_push(seed):
        sub_nodes, g_val = oss(G_unused, node_dict, seed)
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
def VGreedy(G, node_dict, node_x):
    try:
        descendants = nx.descendants(G, node_x)
    except Exception:
        return set(), 0.0
    target_nodes = set(descendants) | {node_x}
    ts_x = node_dict[node_x][0]
    sim_cache = {}
    for u in target_nodes:
        sim_cache[u] = 1.0 if u == node_x else sim(ts_x, node_dict[u][0])
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
def VVGreedy(G, node_dict, k):
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
            sub_nodes, g_value = VGreedy(G_unused, node_dict, node)
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

def PGreedy(G, node_dict, node_x):
    EPS = 1e-12
    try:
        descendants = nx.descendants(G, node_x)
    except Exception:
        return set(), 0.0
    target_nodes = set(descendants) | {node_x}
    ts_x = node_dict[node_x][0]
    sim_cache = {n: (1.0 if n == node_x else sim(ts_x, node_dict[n][0])) for n in target_nodes}
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
    current_S_k = [1.0, float(node_dict[node_x][1]), 1]  # (Σsim, Σvalue, count) 含 node_x
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
def PPGreedy(G, node_dict, k):
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
                sub_nodes, g_value = PGreedy(G_unused, node_dict, seed)
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
        s = float(np.dot(F / nF, G / nG))  # ∈ [-1, 1]
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
def sim(ts1, ts2, method, **kwargs):
    alpha = kwargs.pop("alpha", kwargs.pop("a", 0.0))
    _ = kwargs.get("k", None)
    if not isinstance(method, str) or not method.strip():
        raise ValueError("`method` is required and must be a non-empty string.")
    m = method.strip().lower()
    alias = {
        "euclid": "euclidean",
        "znorm-euclidean": "znorm_euclidean",
        "z-euclid": "znorm_euclidean",
    }
    m = alias.get(m, m)
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

def build_summary_tree(edges, groups_tuple):
    parent, children, nodes = {}, {}, set()
    for p, c in edges:
        nodes.add(p); nodes.add(c)
        parent[c] = p
        children.setdefault(p, []).append(c)
        children.setdefault(c, [])
    roots = [u for u in nodes if u not in parent]
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
                group_nodes_list.append(list(lst))
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
        if len(candidates) > 1:
            candidates.sort(key=lambda x: depth.get(x, float('inf')))
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
    # 按 idx 顺序的组根列表
    group_root_list = [group_root[i] for i in sorted(group_root.keys())]
    return gprime_edges, gprime_root, group_root_list

# ----------------------------
# Flask 初始化
# ----------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_AS_ASCII"] = False  # 返回 JSON 时不转义中文

# 数据保存目录
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
TUPIAN_DIR = os.path.join(os.path.dirname(__file__), "tupian")
os.makedirs(TUPIAN_DIR, exist_ok=True)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
# ----------------------------
# 工具函数（文件保存）
# ----------------------------
def _safe_filename(name: str) -> str:
    """安全文件名"""
    if not isinstance(name, str) or not name:
        return "unnamed"
    name = name.strip()
    name = re.sub(r"[^\w\.-]", "_", name, flags=re.UNICODE)
    return name[:120] or "unnamed"


def _save_record_to_file(record: dict, original_filename: str) -> str:
    """保存记录为 JSON 文件，返回路径"""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_name = _safe_filename(original_filename or "unnamed")
    uid = uuid.uuid4().hex[:8]
    save_path = os.path.join(DATA_DIR, f"{ts}_{safe_name}_{uid}.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return save_path



@app.route("/", methods=["GET"])
def home():
    return render_template("appnewest.html")


@app.route("/writing", methods=["GET"])
def writing_home():
    return render_template("appnewestwriting.html")


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
    try:
        # 获取请求数据
        data = request.get_json(silent=True, force=True)
        if not data:
            print("Received empty JSON")  # 使用 print() 打印日志
            return jsonify({"ok": False, "error": "Empty JSON"}), 400

        filename = data.get("filename") or "unnamed"
        ts_key = data.get("ts_key") or "time_series"
        method = data.get("htsa", {}).get("method", "FDS")
        strategy = data.get("htsa", {}).get("strategy", "V-greedy")
        try:
            a = float(data.get("htsa", {}).get("a", 0))
        except Exception:
            a = 0.0
        k = data.get("htsa", {}).get("k", 1)
        raw_node_dict = data.get("node_dict") or {}
        edges = data.get("edges") or []
        raw_G = data.get("G")

        print(f"Received data: filename={filename}, strategy={strategy}, a={a}, k={k}")

        # 兼容前端的 node_dict 结构（dict: {time_series, value, info}）
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
            node_dict[nid] = (ts, val, info)

        if not node_dict:
            return jsonify({"ok": False, "error": "No valid nodes parsed"}), 400

        # 初始化 sim 函数
        def calculate_sim(ts1, ts2, method, **kwargs):
            return sim(ts1, ts2, method, **kwargs)
        
        G = to_nx_graph(raw_G, node_dict=node_dict)
        if G.number_of_nodes() == 0 and node_dict:
            # 再稳一点：用 node_dict + edges 兜底重建
            G.add_nodes_from(node_dict.keys())
            for u, v in edges:
                if u in node_dict and v in node_dict:
                    G.add_edge(u, v)

        # 根据策略选择对应的算法
        '''if strategy == "V-greedy":
            jieguo = VVGreedy(G, node_dict, k)
        elif strategy == "P-greedy":
            jieguo = PPGreedy(G, node_dict, k)
        elif strategy == "OSS":
            jieguo = ossoss(G, node_dict, k)
        else:
            print(f"Unknown strategy: {strategy}")  # 打印错误信息
            return jsonify({"ok": False, "error": f"Unknown strategy: {strategy}"}), 400'''
        
        # 初始化变量
        used_nodes = set()
        best_subgraphs = []
        total_g_value = 0
        G_unused = G.copy()

        for _round in range(k):
            best_subgraph = None
            best_g_value = -float('inf')
            total_candidates = 0

            for j, node in enumerate(node_dict):
                if node in used_nodes:
                    continue
                total_candidates += 1

                # VGreedy 的展开版 (避免 try-except)
                descendants = nx.descendants(G_unused, node) if node in G_unused else set()
                if not descendants: 
                    sub_nodes, g_value = set(), 0.0
                else:
                    target_nodes = set(descendants) | {node}
                    ts_x = node_dict[node][0]
                    sim_cache = {
                        u: (1.0 if u == node else sim(ts_x, node_dict[u][0], method, a=a))
                        for u in target_nodes
                    }
                    current_subgraph_nodes = {node}
                    current_S_k = [1.0, node_dict[node][1], 1]
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

                    sub_nodes, g_value = current_subgraph_nodes, current_g_value

                # 更新最好的子图
                if g_value > best_g_value:
                    best_g_value = g_value
                    best_subgraph = sub_nodes

            if best_subgraph is None:
                break

            best_subgraphs.append((best_subgraph, best_g_value))
            used_nodes.update(best_subgraph)
            total_g_value += best_g_value

            # 更新图，去除最佳子图的边
            for n in best_subgraph:
                if G_unused.has_node(n):
                    G_unused.remove_edges_from(list(G_unused.in_edges(n)) + list(G_unused.out_edges(n)))

        # 最终结果
        jieguo = (best_subgraphs, total_g_value)
        # 构建总结树，拿到组根列表
        gprime_edges, gprime_root, group_root_list = build_summary_tree(edges, jieguo)

        # 打印总结树的根节点
        print(f"Summary tree built with root: {gprime_root}")
        print(gprime_edges)
        #session['gprime_edges'] = gprime_edges
        #session['gprime_root'] = gprime_root
        # 返回结果并渲染到 result.html
        #return render_template(
            #"result.html",
            #filename=filename,
            #ts_key=ts_key,
            #method=method,
            #strategy=strategy,
            #a=a,
            #k=k,
            #node_dict=node_dict,
            #edges=edges,
            #G=G,#暂时空缺，懒得写了
            #gprime_edges=gprime_edges,
            #gprime_root=gprime_root
        #)
        # 写出摘要边列表，便于手动检查/下载
        txt_filename = f"{filename}_summarized_edges.txt"
        with open(txt_filename, "w", encoding="utf-8") as file:
            for edge in gprime_edges:
                file.write(str(edge) + "\n")

        # 返回 JSON，前端可直接消费
        subgraph_payload = []
        for i, (sg_nodes, g_val) in enumerate(best_subgraphs):
            root_name = group_root_list[i] if i < len(group_root_list) else None
            subgraph_payload.append({
                "root": root_name,
                "nodes": [str(x) for x in sg_nodes],
                "g_value": g_val
            })

        return jsonify({
            "ok": True,
            "gprime_edges": gprime_edges,
            "gprime_root": gprime_root,
            "summary_file": txt_filename,
            "subgraphs": subgraph_payload,
            "group_roots": group_root_list
        })


    except Exception as e:
        print(f"Error during processing: {str(e)}")  # 打印异常信息
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
