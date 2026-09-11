# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Bezier / Coons / Gregory patch evaluation and closest-point solves.

Split out of cage.py unchanged (ST-006). Pure numpy: no FreeCAD, no SdfField.

Parity Note (ST-010):
- Scalar functions (`_coons_grad`, `_bicubic_coons_grad`, `_gregory_grad`, `_closest_on_face`) evaluate a single parameter point or query point (used in interactive handle solves).
- Vectorized functions (`_b` / `_batch` suffixes) evaluate array parameters across multiple points simultaneously (used in CPU mesh grid / ray batch evaluation).
- Parity between scalar and vectorized implementations is strictly verified in test_cage_patch_math_parity.py.
"""
import math
import numpy as np


# ── Bezier helpers ────────────────────────────────────────────────────────────

def _bez3(P0, P1, P2, P3, t):
    u = 1.0 - t
    return u*u*u*P0 + 3*u*u*t*P1 + 3*u*t*t*P2 + t*t*t*P3

def _bez3d(P0, P1, P2, P3, t):
    u = 1.0 - t
    return 3*(u*u*(P1-P0) + 2*u*t*(P2-P1) + t*t*(P3-P2))


def _coons_eval(C0, C1, D0, D1, s, t):
    c0s = _bez3(*C0, s); c1s = _bez3(*C1, s)
    d0t = _bez3(*D0, t); d1t = _bez3(*D1, t)
    P00 = C0[0]; P10 = C0[3]; P01 = C1[0]; P11 = C1[3]
    return ((1-t)*c0s + t*c1s + (1-s)*d0t + s*d1t
            - ((1-s)*(1-t)*P00 + s*(1-t)*P10 + (1-s)*t*P01 + s*t*P11))

def _coons_grad(C0, C1, D0, D1, s, t):
    c0s  = _bez3(*C0, s);  c1s  = _bez3(*C1, s)
    d0t  = _bez3(*D0, t);  d1t  = _bez3(*D1, t)
    c0ds = _bez3d(*C0, s); c1ds = _bez3d(*C1, s)
    d0dt = _bez3d(*D0, t); d1dt = _bez3d(*D1, t)
    P00 = C0[0]; P10 = C0[3]; P01 = C1[0]; P11 = C1[3]
    P = ((1-t)*c0s + t*c1s + (1-s)*d0t + s*d1t
         - ((1-s)*(1-t)*P00 + s*(1-t)*P10 + (1-s)*t*P01 + s*t*P11))
    Ps = ((1-t)*c0ds + t*c1ds + (d1t - d0t)
          + (1-t)*(P00-P10) + t*(P01-P11))
    Pt = ((c1s - c0s) + (1-s)*d0dt + s*d1dt
          + (1-s)*(P00-P01) + s*(P10-P11))
    return P, Ps, Pt


def _hermite(t):
    t2 = t*t
    t3 = t2*t
    h0 = 2*t3 - 3*t2 + 1
    h1 = -2*t3 + 3*t2
    h2 = t3 - 2*t2 + t
    h3 = t3 - t2
    
    dh0 = 6*t2 - 6*t
    dh1 = -6*t2 + 6*t
    dh2 = 3*t2 - 4*t + 1
    dh3 = 3*t2 - 2*t
    return (h0, h1, h2, h3), (dh0, dh1, dh2, dh3)


def _bez3_b(P0, P1, P2, P3, t):
    tN = t[:, None]
    uN = 1.0 - tN
    return uN*uN*uN*P0 + 3.0*uN*uN*tN*P1 + 3.0*uN*tN*tN*P2 + tN*tN*tN*P3

def _bez3d_b(P0, P1, P2, P3, t):
    tN = t[:, None]
    uN = 1.0 - tN
    return 3.0*(uN*uN*(P1-P0) + 2.0*uN*tN*(P2-P1) + tN*tN*(P3-P2))

def _coons_grad_b(C0, C1, D0, D1, s, t):
    c0s = _bez3_b(*C0, s);  c1s = _bez3_b(*C1, s)
    d0t = _bez3_b(*D0, t);  d1t = _bez3_b(*D1, t)
    c0ds = _bez3d_b(*C0, s); c1ds = _bez3d_b(*C1, s)
    d0dt = _bez3d_b(*D0, t); d1dt = _bez3d_b(*D1, t)
    P00 = C0[0]; P10 = C0[3]; P01 = C1[0]; P11 = C1[3]
    sN = s[:, None]; tN = t[:, None]
    P = ((1-tN)*c0s + tN*c1s + (1-sN)*d0t + sN*d1t
         - ((1-sN)*(1-tN)*P00 + sN*(1-tN)*P10 + (1-sN)*tN*P01 + sN*tN*P11))
    Ps = ((1-tN)*c0ds + tN*c1ds + (d1t - d0t) + (1-tN)*(P00-P10) + tN*(P01-P11))
    Pt = ((c1s - c0s) + (1-sN)*d0dt + sN*d1dt + (1-sN)*(P00-P01) + sN*(P10-P11))
    return P, Ps, Pt

def _hermite_b(t):
    t2 = t*t
    t3 = t2*t
    h0 = 2*t3 - 3*t2 + 1
    h1 = -2*t3 + 3*t2
    h2 = t3 - 2*t2 + t
    h3 = t3 - t2
    
    dh0 = 6*t2 - 6*t
    dh1 = -6*t2 + 6*t
    dh2 = 3*t2 - 4*t + 1
    dh3 = 3*t2 - 2*t
    return (h0, h1, h2, h3), (dh0, dh1, dh2, dh3)

def _bicubic_coons_grad_b(C0, C1, D0, D1, s, t):
    sN = s[:, None]
    tN = t[:, None]
    
    c0s = _bez3_b(*C0, s); c0ds = _bez3d_b(*C0, s)
    c1s = _bez3_b(*C1, s); c1ds = _bez3d_b(*C1, s)
    d0t = _bez3_b(*D0, t); d0dt = _bez3d_b(*D0, t)
    d1t = _bez3_b(*D1, t); d1dt = _bez3d_b(*D1, t)
    
    d0_0 = 3.0 * (D0[1] - D0[0])
    d0_1 = 3.0 * (D0[3] - D0[2])
    d1_0 = 3.0 * (D1[1] - D1[0])
    d1_1 = 3.0 * (D1[3] - D1[2])
    
    T0 = (1.0 - sN) * d0_0 + sN * d1_0
    T0ds = d1_0 - d0_0
    T1 = (1.0 - sN) * d0_1 + sN * d1_1
    T1ds = d1_1 - d0_1
    
    c0_0 = 3.0 * (C0[1] - C0[0])
    c0_1 = 3.0 * (C0[3] - C0[2])
    c1_0 = 3.0 * (C1[1] - C1[0])
    c1_1 = 3.0 * (C1[3] - C1[2])
    
    U0 = (1.0 - tN) * c0_0 + tN * c1_0
    U0dt = c1_0 - c0_0
    U1 = (1.0 - tN) * c0_1 + tN * c1_1
    U1dt = c1_1 - c0_1
    
    h_list, dh_list = _hermite_b(s)
    g_list, dg_list = _hermite_b(t)
    
    g0 = g_list[0][:, None]
    g1 = g_list[1][:, None]
    g2 = g_list[2][:, None]
    g3 = g_list[3][:, None]
    
    dg0 = dg_list[0][:, None]
    dg1 = dg_list[1][:, None]
    dg2 = dg_list[2][:, None]
    dg3 = dg_list[3][:, None]
    
    h0 = h_list[0][:, None]
    h1 = h_list[1][:, None]
    h2 = h_list[2][:, None]
    h3 = h_list[3][:, None]
    
    dh0 = dh_list[0][:, None]
    dh1 = dh_list[1][:, None]
    dh2 = dh_list[2][:, None]
    dh3 = dh_list[3][:, None]
    
    R1 = g0 * c0s + g1 * c1s + g2 * T0 + g3 * T1
    R1s = g0 * c0ds + g1 * c1ds + g2 * T0ds + g3 * T1ds
    R1t = dg0 * c0s + dg1 * c1s + dg2 * T0 + dg3 * T1
    
    R2 = h0 * d0t + h1 * d1t + h2 * U0 + h3 * U1
    R2s = dh0 * d0t + dh1 * d1t + dh2 * U0 + dh3 * U1
    R2t = h0 * d0dt + h1 * d1dt + h2 * U0dt + h3 * U1dt
    
    P_corners = [
        [C0[0], C1[0]],
        [C0[3], C1[3]]
    ]
    Ps_corners = [
        [c0_0, c1_0],
        [c0_1, c1_1]
    ]
    Pt_corners = [
        [d0_0, d0_1],
        [d1_0, d1_1]
    ]
    
    N = len(s)
    R12 = np.zeros((N, 3), dtype=np.float64)
    R12s = np.zeros((N, 3), dtype=np.float64)
    R12t = np.zeros((N, 3), dtype=np.float64)
    for i in range(2):
        for j in range(2):
            hi = h_list[i][:, None]
            gj = g_list[j][:, None]
            hi2 = h_list[i+2][:, None]
            gj2 = g_list[j+2][:, None]
            
            dhi = dh_list[i][:, None]
            dhi2 = dh_list[i+2][:, None]
            dgj = dg_list[j][:, None]
            dgj2 = dg_list[j+2][:, None]
            
            R12  += hi * gj * P_corners[i][j] + hi2 * gj * Ps_corners[i][j] + hi * gj2 * Pt_corners[i][j]
            R12s += dhi * gj * P_corners[i][j] + dhi2 * gj * Ps_corners[i][j] + dhi * gj2 * Pt_corners[i][j]
            R12t += hi * dgj * P_corners[i][j] + hi2 * dgj * Ps_corners[i][j] + hi * dgj2 * Pt_corners[i][j]
            
    return R1 + R2 - R12, R1s + R2s - R12s, R1t + R2t - R12t

def _gregory_grad_b(C0, C1, D0, D1, s, t):
    B00 = C0[0]; B10 = C0[1]; B20 = C0[2]; B30 = C0[3]
    B03 = C1[0]; B13 = C1[1]; B23 = C1[2]; B33 = C1[3]
    B01 = D0[1]; B02 = D0[2]
    B31 = D1[1]; B32 = D1[2]

    P11s = B10 + (B01 - B00); P11t = B01 + (B10 - B00)
    P21s = B20 + (B31 - B30); P21t = B31 + (B20 - B30)
    P12s = B13 + (B02 - B03); P12t = B02 + (B13 - B03)
    P22s = B23 + (B32 - B33); P22t = B32 + (B23 - B33)

    den11 = s + t
    ok11 = den11 > 1e-6
    den11_guarded = np.where(ok11, den11, 1.0)
    P11 = np.where(ok11[:, None], (s[:, None] * P11s + t[:, None] * P11t) / den11_guarded[:, None], P11s)
    dP11_ds = np.where(ok11[:, None], t[:, None] * (P11s - P11t) / (den11_guarded[:, None]**2), 0.0)
    dP11_dt = np.where(ok11[:, None], s[:, None] * (P11t - P11s) / (den11_guarded[:, None]**2), 0.0)

    u = 1.0 - s
    den21 = u + t
    ok21 = den21 > 1e-6
    den21_guarded = np.where(ok21, den21, 1.0)
    P21 = np.where(ok21[:, None], (u[:, None] * P21s + t[:, None] * P21t) / den21_guarded[:, None], P21s)
    dP21_ds = np.where(ok21[:, None], -t[:, None] * (P21s - P21t) / (den21_guarded[:, None]**2), 0.0)
    dP21_dt = np.where(ok21[:, None], u[:, None] * (P21t - P21s) / (den21_guarded[:, None]**2), 0.0)

    v = 1.0 - t
    den12 = s + v
    ok12 = den12 > 1e-6
    den12_guarded = np.where(ok12, den12, 1.0)
    P12 = np.where(ok12[:, None], (s[:, None] * P12s + v[:, None] * P12t) / den12_guarded[:, None], P12s)
    dP12_ds = np.where(ok12[:, None], v[:, None] * (P12s - P12t) / (den12_guarded[:, None]**2), 0.0)
    dP12_dt = np.where(ok12[:, None], -s[:, None] * (P12t - P12s) / (den12_guarded[:, None]**2), 0.0)

    den22 = u + v
    ok22 = den22 > 1e-6
    den22_guarded = np.where(ok22, den22, 1.0)
    P22 = np.where(ok22[:, None], (u[:, None] * P22s + v[:, None] * P22t) / den22_guarded[:, None], P22s)
    dP22_ds = np.where(ok22[:, None], -v[:, None] * (P22s - P22t) / (den22_guarded[:, None]**2), 0.0)
    dP22_dt = np.where(ok22[:, None], -u[:, None] * (P22t - P22s) / (den22_guarded[:, None]**2), 0.0)

    B = [
        [B00, B01, B02, B03],
        [B10, P11, P12, B13],
        [B20, P21, P22, B23],
        [B30, B31, B32, B33]
    ]

    dB_ds = [
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)],
        [np.zeros(3), dP11_ds,     dP12_ds,     np.zeros(3)],
        [np.zeros(3), dP21_ds,     dP22_ds,     np.zeros(3)],
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)]
    ]
    dB_dt = [
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)],
        [np.zeros(3), dP11_dt,     dP12_dt,     np.zeros(3)],
        [np.zeros(3), dP21_dt,     dP22_dt,     np.zeros(3)],
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)]
    ]

    bs = np.array([u**3, 3.0 * s * u**2, 3.0 * s**2 * u, s**3])
    bt = np.array([v**3, 3.0 * t * v**2, 3.0 * t**2 * v, t**3])
    
    dbs = np.array([-3.0 * u**2, 3.0 * u**2 - 6.0 * s * u, 6.0 * s * u - 3.0 * s**2, 3.0 * s**2])
    dbt = np.array([-3.0 * v**2, 3.0 * v**2 - 6.0 * t * v, 6.0 * t * v - 3.0 * t**2, 3.0 * t**2])

    N = len(s)
    P_val = np.zeros((N, 3), dtype=np.float64)
    Ps_val = np.zeros((N, 3), dtype=np.float64)
    Pt_val = np.zeros((N, 3), dtype=np.float64)

    for i in range(4):
        for j in range(4):
            bs_i = bs[i][:, None]
            bt_j = bt[j][:, None]
            dbs_i = dbs[i][:, None]
            dbt_j = dbt[j][:, None]
            P_val  += B[i][j] * bs_i * bt_j
            Ps_val += B[i][j] * dbs_i * bt_j + dB_ds[i][j] * bs_i * bt_j
            Pt_val += B[i][j] * bs_i * dbt_j + dB_dt[i][j] * bs_i * bt_j

    return P_val, Ps_val, Pt_val

def _bez_tri3_b(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w):
    uN = u[:, None]
    vN = v[:, None]
    wN = w[:, None]
    return (B300*(uN**3) + B030*(vN**3) + B003*(wN**3)
          + 3.0*B210*(uN**2*vN) + 3.0*B120*(uN*vN**2)
          + 3.0*B021*(vN**2*wN) + 3.0*B012*(vN*wN**2)
          + 3.0*B201*(uN**2*wN) + 3.0*B102*(uN*wN**2)
          + 6.0*B111*(uN*vN*wN))

def _bez_tri3_homo_grads_b(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w):
    uN = u[:, None]
    vN = v[:, None]
    wN = w[:, None]
    dfu = (3.0*uN**2*B300 + 6.0*uN*vN*B210 + 3.0*vN**2*B120
         + 6.0*uN*wN*B201 + 3.0*wN**2*B102 + 6.0*vN*wN*B111)
    dfv = (3.0*vN**2*B030 + 3.0*uN**2*B210 + 6.0*uN*vN*B120
         + 6.0*vN*wN*B021 + 3.0*wN**2*B012 + 6.0*uN*wN*B111)
    dfw = (3.0*wN**2*B003 + 3.0*vN**2*B021 + 6.0*vN*wN*B012
         + 3.0*uN**2*B201 + 6.0*uN*wN*B102 + 6.0*uN*vN*B111)
    return dfu, dfv, dfw



def _bicubic_coons_grad(C0, C1, D0, D1, s, t):
    c0s = _bez3(*C0, s); c0ds = _bez3d(*C0, s)
    c1s = _bez3(*C1, s); c1ds = _bez3d(*C1, s)
    d0t = _bez3(*D0, t); d0dt = _bez3d(*D0, t)
    d1t = _bez3(*D1, t); d1dt = _bez3d(*D1, t)
    
    # Endpoint derivatives for tangent ribbons
    d0_0 = 3.0 * (D0[1] - D0[0])
    d0_1 = 3.0 * (D0[3] - D0[2])
    d1_0 = 3.0 * (D1[1] - D1[0])
    d1_1 = 3.0 * (D1[3] - D1[2])
    
    T0 = (1.0 - s) * d0_0 + s * d1_0
    T0ds = d1_0 - d0_0
    T1 = (1.0 - s) * d0_1 + s * d1_1
    T1ds = d1_1 - d0_1
    
    c0_0 = 3.0 * (C0[1] - C0[0])
    c0_1 = 3.0 * (C0[3] - C0[2])
    c1_0 = 3.0 * (C1[1] - C1[0])
    c1_1 = 3.0 * (C1[3] - C1[2])
    
    U0 = (1.0 - t) * c0_0 + t * c1_0
    U0dt = c1_0 - c0_0
    U1 = (1.0 - t) * c0_1 + t * c1_1
    U1dt = c1_1 - c0_1
    
    h, dh = _hermite(s)
    g, dg = _hermite(t)
    
    R1 = g[0] * c0s + g[1] * c1s + g[2] * T0 + g[3] * T1
    R1s = g[0] * c0ds + g[1] * c1ds + g[2] * T0ds + g[3] * T1ds
    R1t = dg[0] * c0s + dg[1] * c1s + dg[2] * T0 + dg[3] * T1
    
    R2 = h[0] * d0t + h[1] * d1t + h[2] * U0 + h[3] * U1
    R2s = dh[0] * d0t + dh[1] * d1t + dh[2] * U0 + dh[3] * U1
    R2t = h[0] * d0dt + h[1] * d1dt + h[2] * U0dt + h[3] * U1dt
    
    P_corners = [
        [C0[0], C1[0]],
        [C0[3], C1[3]]
    ]
    Ps_corners = [
        [c0_0, c1_0],
        [c0_1, c1_1]
    ]
    Pt_corners = [
        [d0_0, d0_1],
        [d1_0, d1_1]
    ]
    
    R12 = np.zeros(3)
    R12s = np.zeros(3)
    R12t = np.zeros(3)
    for i in range(2):
        for j in range(2):
            R12  += h[i] * g[j] * P_corners[i][j] + h[i+2] * g[j] * Ps_corners[i][j] + h[i] * g[j+2] * Pt_corners[i][j]
            R12s += dh[i] * g[j] * P_corners[i][j] + dh[i+2] * g[j] * Ps_corners[i][j] + dh[i] * g[j+2] * Pt_corners[i][j]
            R12t += h[i] * dg[j] * P_corners[i][j] + h[i+2] * dg[j] * Ps_corners[i][j] + h[i] * dg[j+2] * Pt_corners[i][j]
            
    return R1 + R2 - R12, R1s + R2s - R12s, R1t + R2t - R12t


def _gregory_grad(C0, C1, D0, D1, s, t):
    B00 = C0[0]; B10 = C0[1]; B20 = C0[2]; B30 = C0[3]
    B03 = C1[0]; B13 = C1[1]; B23 = C1[2]; B33 = C1[3]
    B01 = D0[1]; B02 = D0[2]
    B31 = D1[1]; B32 = D1[2]

    P11s = B10 + (B01 - B00); P11t = B01 + (B10 - B00)
    P21s = B20 + (B31 - B30); P21t = B31 + (B20 - B30)
    P12s = B13 + (B02 - B03); P12t = B02 + (B13 - B03)
    P22s = B23 + (B32 - B33); P22t = B32 + (B23 - B33)

    den11 = s + t
    if den11 > 1e-6:
        P11 = (s * P11s + t * P11t) / den11
        dP11_ds = t * (P11s - P11t) / (den11**2)
        dP11_dt = s * (P11t - P11s) / (den11**2)
    else:
        P11 = P11s
        dP11_ds = np.zeros(3)
        dP11_dt = np.zeros(3)

    u = 1.0 - s
    den21 = u + t
    if den21 > 1e-6:
        P21 = (u * P21s + t * P21t) / den21
        dP21_ds = -t * (P21s - P21t) / (den21**2)
        dP21_dt = u * (P21t - P21s) / (den21**2)
    else:
        P21 = P21s
        dP21_ds = np.zeros(3)
        dP21_dt = np.zeros(3)

    v = 1.0 - t
    den12 = s + v
    if den12 > 1e-6:
        P12 = (s * P12s + v * P12t) / den12
        dP12_ds = v * (P12s - P12t) / (den12**2)
        dP12_dt = -s * (P12t - P12s) / (den12**2)
    else:
        P12 = P12s
        dP12_ds = np.zeros(3)
        dP12_dt = np.zeros(3)

    den22 = u + v
    if den22 > 1e-6:
        P22 = (u * P22s + v * P22t) / den22
        dP22_ds = -v * (P22s - P22t) / (den22**2)
        dP22_dt = -u * (P22t - P22s) / (den22**2)
    else:
        P22 = P22s
        dP22_ds = np.zeros(3)
        dP22_dt = np.zeros(3)

    B = [
        [B00, B01, B02, B03],
        [B10, P11, P12, B13],
        [B20, P21, P22, B23],
        [B30, B31, B32, B33]
    ]

    dB_ds = [
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)],
        [np.zeros(3), dP11_ds,     dP12_ds,     np.zeros(3)],
        [np.zeros(3), dP21_ds,     dP22_ds,     np.zeros(3)],
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)]
    ]
    dB_dt = [
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)],
        [np.zeros(3), dP11_dt,     dP12_dt,     np.zeros(3)],
        [np.zeros(3), dP21_dt,     dP22_dt,     np.zeros(3)],
        [np.zeros(3), np.zeros(3), np.zeros(3), np.zeros(3)]
    ]

    bs = np.array([u**3, 3.0 * s * u**2, 3.0 * s**2 * u, s**3])
    bt = np.array([v**3, 3.0 * t * v**2, 3.0 * t**2 * v, t**3])
    
    dbs = np.array([-3.0 * u**2, 3.0 * u**2 - 6.0 * s * u, 6.0 * s * u - 3.0 * s**2, 3.0 * s**2])
    dbt = np.array([-3.0 * v**2, 3.0 * v**2 - 6.0 * t * v, 6.0 * t * v - 3.0 * t**2, 3.0 * t**2])

    P_val = np.zeros(3)
    Ps_val = np.zeros(3)
    Pt_val = np.zeros(3)

    for i in range(4):
        for j in range(4):
            P_val  += B[i][j] * bs[i] * bt[j]
            Ps_val += B[i][j] * dbs[i] * bt[j] + dB_ds[i][j] * bs[i] * bt[j]
            Pt_val += B[i][j] * bs[i] * dbt[j] + dB_dt[i][j] * bs[i] * bt[j]

    return P_val, Ps_val, Pt_val


def _eval_patch_grad(C0, C1, D0, D1, s, t, patch_type):
    if patch_type == 0:
        return _coons_grad(C0, C1, D0, D1, s, t)
    elif patch_type == 1:
        return _bicubic_coons_grad(C0, C1, D0, D1, s, t)
    else:
        return _gregory_grad(C0, C1, D0, D1, s, t)


def _eval_patch(C0, C1, D0, D1, s, t, patch_type):
    if patch_type == 0:
        return _coons_eval(C0, C1, D0, D1, s, t)
    P_val, _, _ = _eval_patch_grad(C0, C1, D0, D1, s, t, patch_type)
    return P_val


def _closest_on_face(C0, C1, D0, D1, q, patch_type=0, simplify=False):
    best_s, best_t, best_dd = 0.5, 0.5, 1e30
    grid_size = 2 if simplify else 4
    step = 1.0 / grid_size
    for si in range(grid_size):
        for ti in range(grid_size):
            s0 = (si + 0.5) * step; t0 = (ti + 0.5) * step
            P = _eval_patch(C0, C1, D0, D1, s0, t0, patch_type)
            dd = np.dot(P - q, P - q)
            if dd < best_dd:
                best_dd = dd; best_s = s0; best_t = t0

    s, t = best_s, best_t
    hit = _eval_patch(C0, C1, D0, D1, s, t, patch_type)
    norm = np.array([0.0, 0.0, 1.0])
    iters = 4 if simplify else 8
    for _ in range(iters):
        P, Ps, Pt = _eval_patch_grad(C0, C1, D0, D1, s, t, patch_type)
        dP = P - q
        gs = float(np.dot(dP, Ps)); gt = float(np.dot(dP, Pt))
        Hss = float(np.dot(Ps, Ps)); Htt = float(np.dot(Pt, Pt)); Hst = float(np.dot(Ps, Pt))
        det = Hss*Htt - Hst*Hst
        if abs(det) < 1e-14:
            break
        s -= (Htt*gs - Hst*gt) / det
        t -= (Hss*gt - Hst*gs) / det
        s = float(max(0.0, min(1.0, s))); t = float(max(0.0, min(1.0, t)))
        hit = P
        n_raw = np.cross(Ps, Pt)
        nlen = np.linalg.norm(n_raw)
        norm = n_raw / nlen if nlen > 1e-12 else norm

    dist = np.linalg.norm(hit - q)
    return hit, norm, dist


# ── Degree-3 Bezier triangle helpers ─────────────────────────────────────────
# Control points B_ijk where i+j+k=3; u=λ0 (weight of v0), v=λ1, w=λ2.
# B300=v0, B030=v1, B003=v2
# B210,B120 = handles along edge v0→v1 (at 1/3, 2/3)
# B021,B012 = handles along edge v1→v2
# B102,B201 = handles along edge v2→v0 (B102 at 1/3 from v2, B201 at 2/3 from v2)
# B111      = interior handle

def _bez_tri3(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w):
    return (B300*(u**3) + B030*(v**3) + B003*(w**3)
          + 3.0*B210*(u**2*v) + 3.0*B120*(u*v**2)
          + 3.0*B021*(v**2*w) + 3.0*B012*(v*w**2)
          + 3.0*B201*(u**2*w) + 3.0*B102*(u*w**2)
          + 6.0*B111*(u*v*w))

def _bez_tri3_homo_grads(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w):
    """Homogeneous partial derivatives ∂/∂u, ∂/∂v, ∂/∂w (treating u,v,w independent)."""
    dfu = (3.0*u**2*B300 + 6.0*u*v*B210 + 3.0*v**2*B120
         + 6.0*u*w*B201 + 3.0*w**2*B102 + 6.0*v*w*B111)
    dfv = (3.0*v**2*B030 + 3.0*u**2*B210 + 6.0*u*v*B120
         + 6.0*v*w*B021 + 3.0*w**2*B012 + 6.0*u*w*B111)
    dfw = (3.0*w**2*B003 + 3.0*v**2*B021 + 6.0*v*w*B012
         + 3.0*u**2*B201 + 6.0*u*w*B102 + 6.0*u*v*B111)
    return dfu, dfv, dfw

def _closest_on_tri_face(ctrl_pts, q, simplify=False):
    """Find closest point on a degree-3 Bezier triangle to q.

    ctrl_pts: (B300, B030, B003, B210, B120, B021, B012, B201, B102, B111)
    Returns: (hit_point, normal, unsigned_distance)
    """
    B300, B030, B003, B210, B120, B021, B012, B201, B102, B111 = ctrl_pts

    # Grid search for initial guess (triangular domain samples)
    if simplify:
        candidates = [(1.0/3, 1.0/3)]
    else:
        candidates = [
            (1.0/3, 1.0/3), (0.6, 0.2), (0.2, 0.6), (0.2, 0.2),
            (0.8, 0.1), (0.1, 0.8), (0.1, 0.1),
        ]
    best_u, best_v, best_dd = 1.0/3, 1.0/3, 1e30
    for su, sv in candidates:
        sw = 1.0 - su - sv
        if sw < 0.0:
            continue
        P = _bez_tri3(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, su, sv, sw)
        dd = float(np.dot(P - q, P - q))
        if dd < best_dd:
            best_dd = dd; best_u = su; best_v = sv

    u, v = best_u, best_v
    norm = np.array([0.0, 0.0, 1.0])
    hit = np.array(q, dtype=np.float64)

    iters = 4 if simplify else 10
    for _ in range(iters):
        w = 1.0 - u - v
        P = _bez_tri3(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w)
        dfu, dfv, dfw = _bez_tri3_homo_grads(
            B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w)
        # Parametric partials: d/du = dfu - dfw, d/dv = dfv - dfw
        Pu = dfu - dfw
        Pv = dfv - dfw
        n_raw = np.cross(Pu, Pv)
        nlen = float(np.linalg.norm(n_raw))
        if nlen > 1e-12:
            norm = n_raw / nlen

        dP = P - q
        gs = float(np.dot(dP, Pu)); gt = float(np.dot(dP, Pv))
        Hss = float(np.dot(Pu, Pu)); Htt = float(np.dot(Pv, Pv)); Hst = float(np.dot(Pu, Pv))
        det = Hss * Htt - Hst * Hst
        hit = P
        if abs(det) < 1e-14:
            break
        u -= (Htt * gs - Hst * gt) / det
        v -= (Hss * gt - Hst * gs) / det
        # Clamp to triangular domain
        u = float(max(0.0, min(1.0, u)))
        v = float(max(0.0, min(1.0, v)))
        if u + v > 1.0:
            sc = 1.0 / (u + v)
            u *= sc; v *= sc

    dist = float(np.linalg.norm(hit - q))
    return hit, norm, dist


def _eval_patch_grad_b(C0, C1, D0, D1, s, t, patch_type):
    if patch_type == 0:
        return _coons_grad_b(C0, C1, D0, D1, s, t)
    elif patch_type == 1:
        return _bicubic_coons_grad_b(C0, C1, D0, D1, s, t)
    else:
        return _gregory_grad_b(C0, C1, D0, D1, s, t)

def _closest_on_bilinear_batch(v0, v1, v2, v3, q, simplify=False):
    if len(q) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0)
    
    # 1. Build initial grid to find best starting seed
    grid_size = 2 if simplify else 4
    step = 1.0 / grid_size
    grid_pts = []
    grid_params = []
    for si in range(grid_size):
        for ti in range(grid_size):
            s0 = (si + 0.5) * step
            t0 = (ti + 0.5) * step
            # P(s,t) = (1-t)*((1-s)v0 + s*v1) + t*((1-s)v3 + s*v2)
            P = (1.0 - t0) * ((1.0 - s0) * v0 + s0 * v1) + t0 * ((1.0 - s0) * v3 + s0 * v2)
            grid_pts.append(P)
            grid_params.append((s0, t0))
    grid_pts = np.array(grid_pts)
    
    diff = q[:, None, :] - grid_pts[None, :, :]
    dist_sq = (diff ** 2).sum(axis=-1)
    idx = np.argmin(dist_sq, axis=-1)
    
    s = np.array([grid_params[i][0] for i in idx])
    t = np.array([grid_params[i][1] for i in idx])
    
    norm = np.zeros((len(q), 3), dtype=np.float64)
    norm[:, 2] = 1.0
    hit = np.array(q, dtype=np.float64)
    
    iters = 3 if simplify else 5
    for _ in range(iters):
        # Bilinear patch evaluation
        # P(s,t) = (1-t)*((1-s)v0 + s*v1) + t*((1-s)v3 + s*v2)
        # Ps = (1-t)*(v1 - v0) + t*(v2 - v3)
        # Pt = (1-s)*(v3 - v0) + s*(v2 - v1)
        s_col = s[:, None]
        t_col = t[:, None]
        P = (1.0 - t_col) * ((1.0 - s_col) * v0 + s_col * v1) + t_col * ((1.0 - s_col) * v3 + s_col * v2)
        Ps = (1.0 - t_col) * (v1 - v0) + t_col * (v2 - v3)
        Pt = (1.0 - s_col) * (v3 - v0) + s_col * (v2 - v1)
        
        dP = P - q
        gs = (dP * Ps).sum(-1)
        gt = (dP * Pt).sum(-1)
        Hss = (Ps * Ps).sum(-1)
        Htt = (Pt * Pt).sum(-1)
        Hst = (Ps * Pt).sum(-1)
        det = Hss * Htt - Hst * Hst
        
        hit = P
        n_raw = np.cross(Ps, Pt)
        nlen = np.linalg.norm(n_raw, axis=-1, keepdims=True)
        ok_n = nlen[:, 0] > 1e-12
        norm[ok_n] = n_raw[ok_n] / nlen[ok_n]
        
        ok = np.abs(det) > 1e-14
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        
        s = np.clip(s - (Htt * gs - Hst * gt) * inv, 0.0, 1.0)
        t = np.clip(t - (Hss * gt - Hst * gs) * inv, 0.0, 1.0)
        
    dist = np.linalg.norm(hit - q, axis=-1)
    return hit, norm, dist

def _closest_on_face_batch(C0, C1, D0, D1, q, patch_type=0, simplify=False):
    if len(q) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0)
        
    grid_size = 2 if simplify else 4
    step = 1.0 / grid_size
    grid_pts = []
    grid_params = []
    for si in range(grid_size):
        for ti in range(grid_size):
            s0 = (si + 0.5) * step; t0 = (ti + 0.5) * step
            P = _eval_patch(C0, C1, D0, D1, s0, t0, patch_type)
            grid_pts.append(P)
            grid_params.append((s0, t0))
    grid_pts = np.array(grid_pts)
    
    diff = q[:, None, :] - grid_pts[None, :, :]
    dist_sq = (diff ** 2).sum(axis=-1)
    idx = np.argmin(dist_sq, axis=-1)
    
    s = np.array([grid_params[i][0] for i in idx])
    t = np.array([grid_params[i][1] for i in idx])
    
    norm = np.zeros((len(q), 3), dtype=np.float64)
    norm[:, 2] = 1.0
    hit = np.array(q, dtype=np.float64)
    
    iters = 4 if simplify else 8
    for _ in range(iters):
        P, Ps, Pt = _eval_patch_grad_b(C0, C1, D0, D1, s, t, patch_type)
        dP = P - q
        gs = (dP * Ps).sum(-1)
        gt = (dP * Pt).sum(-1)
        Hss = (Ps * Ps).sum(-1)
        Htt = (Pt * Pt).sum(-1)
        Hst = (Ps * Pt).sum(-1)
        det = Hss*Htt - Hst*Hst
        
        hit = P
        n_raw = np.cross(Ps, Pt)
        nlen = np.linalg.norm(n_raw, axis=-1, keepdims=True)
        ok_n = nlen[:, 0] > 1e-12
        norm[ok_n] = n_raw[ok_n] / nlen[ok_n]
        
        ok = np.abs(det) > 1e-14
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        
        s = np.clip(s - (Htt * gs - Hst * gt) * inv, 0.0, 1.0)
        t = np.clip(t - (Hss * gt - Hst * gs) * inv, 0.0, 1.0)
        
    dist = np.linalg.norm(hit - q, axis=-1)
    return hit, norm, dist

def _closest_on_tri_face_batch(ctrl_pts, q, simplify=False):
    if len(q) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0)
        
    B300, B030, B003, B210, B120, B021, B012, B201, B102, B111 = ctrl_pts
    if simplify:
        candidates = [(1.0/3, 1.0/3)]
    else:
        candidates = [
            (1.0/3, 1.0/3), (0.6, 0.2), (0.2, 0.6), (0.2, 0.2),
            (0.8, 0.1), (0.1, 0.8), (0.1, 0.1),
        ]
    
    grid_pts = []
    grid_params = []
    for su, sv in candidates:
        sw = 1.0 - su - sv
        if sw < 0.0:
            continue
        P = _bez_tri3(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, su, sv, sw)
        grid_pts.append(P)
        grid_params.append((su, sv))
    grid_pts = np.array(grid_pts)
    
    diff = q[:, None, :] - grid_pts[None, :, :]
    dist_sq = (diff ** 2).sum(axis=-1)
    idx = np.argmin(dist_sq, axis=-1)
    
    u = np.array([grid_params[i][0] for i in idx])
    v = np.array([grid_params[i][1] for i in idx])
    
    norm = np.zeros((len(q), 3), dtype=np.float64)
    norm[:, 2] = 1.0
    hit = np.array(q, dtype=np.float64)
    
    iters = 4 if simplify else 10
    for _ in range(iters):
        w = 1.0 - u - v
        P = _bez_tri3_b(B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w)
        dfu, dfv, dfw = _bez_tri3_homo_grads_b(
            B300, B030, B003, B210, B120, B021, B012, B201, B102, B111, u, v, w)
        Pu = dfu - dfw
        Pv = dfv - dfw
        n_raw = np.cross(Pu, Pv)
        nlen = np.linalg.norm(n_raw, axis=-1, keepdims=True)
        ok_n = nlen[:, 0] > 1e-12
        norm[ok_n] = n_raw[ok_n] / nlen[ok_n]
        
        dP = P - q
        gs = (dP * Pu).sum(-1)
        gt = (dP * Pv).sum(-1)
        Hss = (Pu * Pu).sum(-1)
        Htt = (Pv * Pv).sum(-1)
        Hst = (Pu * Pv).sum(-1)
        det = Hss * Htt - Hst * Hst
        
        hit = P
        
        ok = np.abs(det) > 1e-14
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        
        u = u - (Htt * gs - Hst * gt) * inv
        v = v - (Hss * gt - Hst * gs) * inv
        
        u = np.clip(u, 0.0, 1.0)
        v = np.clip(v, 0.0, 1.0)
        
        uv = u + v
        exceeded = uv > 1.0
        if exceeded.any():
            sc = 1.0 / np.where(exceeded, uv, 1.0)
            u = np.where(exceeded, u * sc, u)
            v = np.where(exceeded, v * sc, v)
            
    dist = np.linalg.norm(hit - q, axis=-1)
    return hit, norm, dist
