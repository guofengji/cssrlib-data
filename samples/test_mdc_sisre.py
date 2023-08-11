"""
Signal-In-Space Range Error for QZSS MADOCA
"""
from copy import deepcopy
import matplotlib.pyplot as plt
import numpy as np
import sys

from cssrlib.ephemeris import eph2pos, findeph
from cssrlib.gnss import Nav, sat2prn, sat2id, vnorm
from cssrlib.gnss import time2gpst, time2doy, epoch2time, time2str
from cssrlib.gnss import timeadd, timediff
from cssrlib.gnss import uGNSS as ug
from cssrlib.gnss import rSigRnx, uTYP, rCST, sys2str
from cssrlib.peph import atxdec
from cssrlib.peph import peph, biasdec, apc2com
from cssrlib.cssrlib import cssr
from cssrlib.cssrlib import sCType, sSigGPS
from cssrlib.cssrlib import sCSSRTYPE as sc
from cssrlib.rinex import rnxdec
from binascii import unhexlify


# Start epoch and number of epochs
#
ep = [2023, 7, 8, 4, 0, 0]

time = epoch2time(ep)
year = ep[0]
doy = int(time2doy(time))

nep = 900*2
step = 1

navfile = '../data/BRDC00IGS_R_20231890000_01D_MN.rnx'

ac = 'COD0OPSFIN'
#ac = 'COD0OPSRAP'
#ac = 'COD0MGXFIN'

orbfile = '../data/{}_{:4d}{:03d}0000_01D_05M_ORB.SP3'\
    .format(ac, year, doy)

clkfile = '../data/{}_{:4d}{:03d}0000_01D_30S_CLK.CLK'\
    .format(ac, year, doy)

bsxfile = '../data/{}_{:4d}{:03d}0000_01D_01D_OSB.BIA'\
    .format(ac, year, doy)

file_l6 = '../data/qzsl6_189e.txt'
dtype = [('wn', 'int'), ('tow', 'int'), ('prn', 'int'),
         ('type', 'int'), ('len', 'int'), ('nav', 'S500')]
v = np.genfromtxt(file_l6, dtype=dtype)

prn_ref = 199  # QZSS PRN
l6_ch = 1  # 0:L6D, 1:L6E

if time > epoch2time([2022, 11, 27, 0, 0, 0]):
    atxfile = '../data/igs20.atx'
else:
    atxfile = '../data/igs14.atx'

rnx = rnxdec()
nav = Nav()
orb = peph()

# Decode RINEX NAV data
#
nav = rnx.decode_nav(navfile, nav)

# Load precise orbits and clock offsets
#
nav = orb.parse_sp3(orbfile, nav)
nav = rnx.decode_clk(clkfile, nav)

# Load code and phase biases from Bias-SINEX
#
bsx = biasdec()
bsx.parse(bsxfile)

# Setup SSR decoder
#
cs = cssr()
cs.monlevel = 2

# Load ANTEX data for satellites and stations
#
atx = atxdec()
atx.readpcv(atxfile)

# Set PCO/PCV information
#
nav.sat_ant = atx.pcvs

# Intialize data structures for results
#
t = np.zeros(nep)
orb_r = np.zeros((nep, ug.MAXSAT))*np.nan
orb_a = np.zeros((nep, ug.MAXSAT))*np.nan
orb_c = np.zeros((nep, ug.MAXSAT))*np.nan
clk = np.zeros((nep, ug.MAXSAT))*np.nan

ns2m = rCST.CLIGHT*1e-9

# Loop over number of epochs from start time
#
for ne in range(nep):

    print(time2str(time))

    week, tow = time2gpst(time)
    cs.week = week
    cs.tow0 = tow//3600*3600

    # Set initial epoch
    #
    if ne == 0:
        t0 = deepcopy(time)
        t0.time = t0.time//30*30
        nav.time_p = t0

    vi = v[(v['tow'] == tow) & (v['type'] == l6_ch) & (v['prn'] == prn_ref)]
    msg = unhexlify(vi['nav'][0])
    cs.decode_l6msg(msg, 0)
    if cs.fcnt == 5:  # end of sub-frame
        cs.decode_cssr(cs.buff, 0)

    # Extract SSR corrections
    #
    if (cs.lc[0].cstat & 0xf) == 0xf:

        ns = len(cs.sat_n)

        rs0 = np.ones((ns, 3))*np.nan
        vs0 = np.ones((ns, 3))*np.nan
        dts0 = np.ones((ns, 1))*np.nan

        rs = np.ones((ns, 3))*np.nan
        vs = np.ones((ns, 3))*np.nan
        dts = np.ones((ns, 1))*np.nan

        d_rs = np.ones((ns, 3))*np.nan
        d_dts = np.ones((ns, 1))*np.nan

        for j, sat in enumerate(cs.sat_n):

            sys, _ = sat2prn(sat)

            # Precise reference orbit and clock
            #
            rs_, dts_, _ = orb.peph2pos(time, sat, nav)
            if rs_ is None or dts is None:
                continue
            rs0[j, :] = rs_[0:3]
            vs0[j, :] = rs_[3:6]
            dts0[j] = dts_[0]

            """
            print("{} {} prec xyz [m] {:14.3f} {:14.3f}m {:14.3f} clk [ms] {:12.6f}"
                  .format(time2str(time), sat2id(sat),
                          rs0[j,0], rs0[j,1], rs0[j,2], dts0[j]*1e6))
            """

            # Broadcast ephemeris IODE, orbit and clock corrections
            #
            idx = cs.sat_n.index(sat)
            iode = cs.lc[0].iode[idx]
            dorb = cs.lc[0].dorb[idx, :]  # radial,along-track,cross-track

            if cs.cssrmode == sc.GAL_HAS_SIS:  # HAS only
                if cs.mask_id != cs.mask_id_clk:  # mask has changed
                    if sat not in cs.sat_n_p:
                        continue
                    idx = cs.sat_n_p.index(sat)

            dclk = cs.lc[0].dclk[idx]

            if sys not in cs.nav_mode.keys():
                continue
            mode = cs.nav_mode[sys]

            # Get position, velocity and clock offset from broadcast ephemerides
            #
            eph = findeph(nav.eph, time, sat, iode, mode=mode)
            if eph is None:
                print("ERROR: cannot find BRDC for {}".format(sat2id(sat)))
                continue

            rs[j, :], vs[j, :], dts[j] = eph2pos(time, eph, True)

            """
            dt_rel = -2*(rs[j, :]@vs[j, :])/rCST.CLIGHT**2
            dts[j] += dt_rel
            """

            """
            print("{} {} brdc xyz [m] {:14.3f} {:14.3f}m {:14.3f} clk [ms] {:12.6f}"
                  .format(time2str(time), sat2id(sat),
                          rs[j,0], rs[j,1], rs[j,2], dts[j]*1e6))
            """

            # Select PCO reference signals for QZSS MADOCA
            #
            if sys == ug.GPS:
                sig0 = (rSigRnx("GC1W"), rSigRnx("GC2W"))
            elif sys == ug.GAL:
                sig0 = (rSigRnx("EC1C"), rSigRnx("EC7Q"))
            elif sys == ug.QZS:
                sig0 = (rSigRnx("JC1C"), rSigRnx("JC2S"))
            else:
                print("ERROR: invalid sytem {}".format(sys2str(sys)))
                continue

            # Convert to CoM using ANTEX PCO corrections
            #
            rs[j, :] += apc2com(nav, sat, time, rs[j, :], sig0)

            # Select user reference signals
            #
            if sys == ug.GPS:
                sigs = (rSigRnx("GC1C"), rSigRnx("GC2W"))
            elif sys == ug.GAL:
                sigs = (rSigRnx("EC1C"), rSigRnx("EC5Q"))
            elif sys == ug.QZS:
                sigs = (rSigRnx("JC1C"), rSigRnx("JC2S"))
            else:
                print("ERROR: invalid sytem {}".format(sys2str(sys)))
                continue

            freq = [s.frequency() for s in sigs]
            facs = (+freq[0]**2/(freq[0]**2-freq[1]**2),
                    -freq[1]**2/(freq[0]**2-freq[1]**2))

            # Get HAS biases
            #
            idx_n_ = np.where(np.array(cs.sat_n) == sat)[0]
            if len(idx_n_) == 0:
                continue
            idx_n = idx_n_[0]

            kidx = [-1]*nav.nf
            nsig = 0
            for k, sig in enumerate(cs.sig_n[idx_n]):
                if sig < 0:
                    continue
                for f in range(len(sigs)):
                    if cs.cssrmode == sc.GAL_HAS_SIS and sys == ug.GPS and \
                            sig == sSigGPS.L2P:
                        sig = sSigGPS.L2W  # work-around
                    if cs.ssig2rsig(sys, uTYP.C, sig) == sigs[f]:
                        kidx[f] = k
                        nsig += 1
                    elif cs.ssig2rsig(sys, uTYP.C, sig) == sigs[f].toAtt('X'):
                        kidx[f] = k
                        nsig += 1
            if nsig >= nav.nf:
                if cs.lc[0].cstat & (1 << sCType.CBIAS) == (1 << sCType.CBIAS):
                    cbias = cs.lc[0].cbias[idx_n][kidx]

            if np.all(cs.lc[0].dorb[idx_n] == np.array([0.0, 0.0, 0.0])):
                continue

            # Get CODE biases
            #
            cbias_ = np.ones(len(sigs))*np.nan
            for i, sig in enumerate(sigs):
                cbias_[i] = bsx.getosb(sat, time, sig)*ns2m

            dcb = cbias[0]-cbias[1]
            dcb_ = cbias_[0]-cbias_[1]
            osbIF = facs[0]*cbias[0]+facs[1]*cbias[1]
            osbIF_ = facs[0]*cbias_[0]+facs[1]*cbias_[1]

            # Apply ionosphere-free bias correction to clock offsets
            #
            dts[j] -= osbIF/rCST.CLIGHT*-1.0  # switch sign of SSR biases
            dts0[j] -= osbIF_/rCST.CLIGHT

            # Along-track, cross-track and radial conversion
            #
            ea = vnorm(vs[j, :])
            rc = np.cross(rs[j, :], vs[j, :])
            ec = vnorm(rc)
            er = np.cross(ea, ec)
            A = np.array([er, ea, ec])

            # Convert orbit corrections from orbital frame to ECEF
            #
            dorb_e = dorb@A

            # Apply SSR correction
            #
            rs[j, :] -= dorb_e
            dts[j] += dclk/rCST.CLIGHT  # [m] -> [s]

            """
            print("{} {} ssrc xyz [m] {:14.3f} {:14.3f}m {:14.3f} clk [ms] {:12.6f}"
                  .format(time2str(time), sat2id(sat),
                          rs[j, 0], rs[j, 1], rs[j, 2], dclk))
            """

            # HAS vs. precise
            #
            d_rs[j, :] = (rs[j, :] - rs0[j, :])@A.T
            d_dts[j, 0] = dts[j, 0] - dts0[j, 0]

            print("{} {} diff rac [m] {:8.3f} {:8.3f} {:8.3f} "
                  "clk [m] {:12.6f} "
                  "bias SSR [m] {} {:7.3f} {} {:7.3f} DCB {:7.3f} "
                  "bias COD [m] {} {:7.3f} {} {:7.3f} DCB {:7.3f} "
                  .format(time2str(time), sat2id(sat),
                          d_rs[j, 0], d_rs[j, 1], d_rs[j, 2],
                          d_dts[j, 0]*rCST.CLIGHT,
                          sigs[0], cbias[0], sigs[1], cbias[1], osbIF,
                          sigs[0], cbias_[0], sigs[1], cbias_[1], osbIF_))

            orb_r[ne, sat-1] = d_rs[j, 0]
            orb_a[ne, sat-1] = d_rs[j, 1]
            orb_c[ne, sat-1] = d_rs[j, 2]
            clk[ne, sat-1] = d_dts[j, 0]*rCST.CLIGHT

    print()

    # Save output
    #
    t[ne] = timediff(time, t0)/60

    # Next time-step
    #
    time = timeadd(time, step)

fig = plt.figure(figsize=[7, 9])
fig.set_rasterized(True)

lbl_t = ['Radial [m]', 'Along [m]', 'Cross [m]', 'Clock [m]']

plt.subplot(4, 1, 1)
plt.plot(t, orb_r[:, ug.GPSMIN:ug.GPSMIN+ug.GPSMAX], 'k.', label='GPS')
plt.plot(t, orb_r[:, ug.GALMIN:ug.GALMIN+ug.GALMAX], 'b.', label='GAL')
plt.ylabel(lbl_t[0])
plt.grid()

plt.subplot(4, 1, 2)
plt.plot(t, orb_a[:, ug.GPSMIN:ug.GPSMIN+ug.GPSMAX], 'k.', label='GPS')
plt.plot(t, orb_a[:, ug.GALMIN:ug.GALMIN+ug.GALMAX], 'b.', label='GAL')
plt.ylabel(lbl_t[1])
plt.grid()

plt.subplot(4, 1, 3)
plt.plot(t, orb_c[:, ug.GPSMIN:ug.GPSMIN+ug.GPSMAX], 'k.', label='GPS')
plt.plot(t, orb_c[:, ug.GALMIN:ug.GALMIN+ug.GALMAX], 'b.', label='GAL')
plt.ylabel(lbl_t[2])
plt.grid()

plt.subplot(4, 1, 4)
plt.plot(t, clk[:, ug.GPSMIN:ug.GPSMIN+ug.GPSMAX], 'k.', label='GPS')
plt.plot(t, clk[:, ug.GALMIN:ug.GALMIN+ug.GALMAX], 'b.', label='GAL')
plt.ylabel(lbl_t[3])
plt.grid()

plotFileFormat = 'eps'
plotFileName = '.'.join(('test_mdc_sisre', plotFileFormat))

plt.savefig(plotFileName, format=plotFileFormat, bbox_inches='tight', dpi=300)
# plt.show()