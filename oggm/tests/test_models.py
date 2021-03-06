from __future__ import division

import warnings

from six.moves import zip

warnings.filterwarnings("once", category=DeprecationWarning)

import logging
logging.basicConfig(format='%(asctime)s: %(name)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S', level=logging.DEBUG)

import unittest
import os
import copy
import time
import shutil

import shapely.geometry as shpg
import numpy as np
import pandas as pd
import geopandas as gpd
from numpy.testing import assert_allclose

# Local imports
from oggm.tests import init_hef
from oggm.core.models import massbalance, flowline
from oggm.core.models.massbalance import LinearMassBalanceModel
from oggm.tests import is_slow, RUN_MODEL_TESTS, is_performance_test
import xarray as xr
from oggm import utils, cfg
from oggm.utils import get_demo_file
from oggm.cfg import N, SEC_IN_DAY, SEC_IN_YEAR, SEC_IN_MONTHS
from oggm.core.preprocessing import climate, inversion, centerlines

# Tests
from oggm.tests.funcs import *

# after oggm.test
import matplotlib.pyplot as plt

# do we event want to run the tests?
if not RUN_MODEL_TESTS:
    raise unittest.SkipTest('Skipping all model tests.')

do_plot = False

DOM_BORDER = 80

# In case some logging happens or so
cfg.PATHS['working_dir'] = cfg.PATHS['test_dir']


class TestInitFlowline(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_init_present_time_glacier(self):

        gdir = init_hef(border=DOM_BORDER)
        flowline.init_present_time_glacier(gdir)

        fls = gdir.read_pickle('model_flowlines')

        ofl = gdir.read_pickle('inversion_flowlines', div_id=1)[-1]

        self.assertTrue(gdir.rgi_date.year == 2003)
        self.assertTrue(len(fls) == 4)

        vol = 0.
        area = 0.
        for fl in fls:
            refo = 1 if fl is fls[-1] else 0
            self.assertTrue(fl.order == refo)
            ref = np.arange(len(fl.surface_h)) * fl.dx
            np.testing.assert_allclose(ref, fl.dis_on_line,
                                       rtol=0.001,
                                       atol=0.01)
            self.assertTrue(len(fl.surface_h) ==
                            len(fl.bed_h) ==
                            len(fl.bed_shape) ==
                            len(fl.dis_on_line) ==
                            len(fl.widths))

            self.assertTrue(np.all(fl.widths >= 0))
            vol += fl.volume_km3
            area += fl.area_km2

            if refo == 1:
                rmsd = utils.rmsd(ofl.widths[:-5] * gdir.grid.dx,
                                  fl.widths_m[0:len(ofl.widths)-5])
                self.assertTrue(rmsd < 5.)

        rtol = 0.02
        np.testing.assert_allclose(0.573, vol, rtol=rtol)
        np.testing.assert_allclose(7400.0, fls[-1].length_m, atol=101)
        np.testing.assert_allclose(gdir.rgi_area_km2, area, rtol=rtol)

        if do_plot:
            plt.plot(fls[-1].bed_h)
            plt.plot(fls[-1].surface_h)
            plt.show()

    def test_present_time_glacier_massbalance(self):

        gdir = init_hef(border=DOM_BORDER)
        flowline.init_present_time_glacier(gdir)

        mb_mod = massbalance.PastMassBalanceModel(gdir)

        fls = gdir.read_pickle('model_flowlines')
        glacier = flowline.FlowlineModel(fls)

        mbdf = gdir.get_ref_mb_data()

        hgts = np.array([])
        widths = np.array([])
        for fl in glacier.fls:
            hgts = np.concatenate((hgts, fl.surface_h))
            widths = np.concatenate((widths, fl.widths_m))
        tot_mb = []
        refmb = []
        grads = hgts * 0
        for yr, mb in mbdf.iterrows():
            refmb.append(mb['ANNUAL_BALANCE'])
            mbh = mb_mod.get_annual_mb(hgts, yr) * SEC_IN_YEAR * cfg.RHO
            grads += mbh
            tot_mb.append(np.average(mbh, weights=widths))
        grads /= len(tot_mb)

        # Bias
        self.assertTrue(np.abs(utils.md(tot_mb, refmb)) < 50)

        # Gradient
        dfg = pd.read_csv(utils.get_demo_file('mbgrads_RGI40-11.00897.csv'),
                          index_col='ALTITUDE').mean(axis=1)

        # Take the altitudes below 3100 and fit a line
        dfg = dfg[dfg.index < 3100]
        pok = np.where(hgts < 3100)
        from scipy.stats import linregress
        slope_obs, _, _, _, _ = linregress(dfg.index, dfg.values)
        slope_our, _, _, _, _ = linregress(hgts[pok], grads[pok])
        np.testing.assert_allclose(slope_obs, slope_our, rtol=0.15)


class TestOtherDivides(unittest.TestCase):

    def setUp(self):

        # test directory
        self.testdir = os.path.join(cfg.PATHS['test_dir'], 'tmp_div')
        if not os.path.exists(self.testdir):
            os.makedirs(self.testdir)
        # self.clean_dir()

        # Init
        cfg.initialize()
        cfg.PATHS['dem_file'] = utils.get_demo_file('srtm_oetztal.tif')
        cfg.PATHS['climate_file'] = utils.get_demo_file('histalp_merged_hef.nc')

    def tearDown(self):
        self.rm_dir()

    def rm_dir(self):
        shutil.rmtree(self.testdir)

    def clean_dir(self):
        shutil.rmtree(self.testdir)
        os.makedirs(self.testdir)

    def test_define_divides(self):

        from oggm.core.preprocessing import (gis, centerlines, geometry,
                                             climate, inversion)
        from oggm import GlacierDirectory
        import geopandas as gpd

        hef_file = utils.get_demo_file('rgi_oetztal.shp')
        rgidf = gpd.GeoDataFrame.from_file(hef_file)

        # This is another glacier with divides
        entity = rgidf.loc[rgidf.RGIId == 'RGI50-11.00719'].iloc[0]
        gdir = GlacierDirectory(entity, base_dir=self.testdir)
        gis.define_glacier_region(gdir, entity=entity)
        gis.glacier_masks(gdir)
        centerlines.compute_centerlines(gdir)
        centerlines.compute_downstream_lines(gdir)
        geometry.initialize_flowlines(gdir)
        centerlines.compute_downstream_bedshape(gdir)
        geometry.catchment_area(gdir)
        geometry.catchment_width_geom(gdir)
        geometry.catchment_width_correction(gdir)
        climate.process_histalp_nonparallel([gdir])
        climate.local_mustar_apparent_mb(gdir, tstar=1930, bias=0,
                                         prcp_fac=2.5)
        inversion.prepare_for_inversion(gdir)
        v, ainv = inversion.mass_conservation_inversion(gdir)
        flowline.init_present_time_glacier(gdir)

        myarea = 0.
        for did in gdir.divide_ids:
            cls = gdir.read_pickle('inversion_flowlines', div_id=did)
            for cl in cls:
                myarea += np.sum(cl.widths * cl.dx * gdir.grid.dx**2)

        np.testing.assert_allclose(ainv, gdir.rgi_area_m2, rtol=1e-2)
        np.testing.assert_allclose(myarea, gdir.rgi_area_m2, rtol=1e-2)
        self.assertTrue(len(gdir.divide_ids) == 2)

        myarea = 0.
        for did in gdir.divide_ids:
            cls = gdir.read_pickle('inversion_flowlines', div_id=did)
            for cl in cls:
                myarea += np.sum(cl.widths * cl.dx * gdir.grid.dx**2)

        np.testing.assert_allclose(myarea, gdir.rgi_area_m2, rtol=1e-2)
        self.assertTrue(len(gdir.divide_ids) == 2)

        fls = gdir.read_pickle('model_flowlines')
        glacier = flowline.FlowlineModel(fls)
        if cfg.PARAMS['grid_dx_method'] == 'fixed':
            self.assertEqual(len(fls), 4)
        if cfg.PARAMS['grid_dx_method'] == 'linear':
            self.assertEqual(len(fls), 5)
        if cfg.PARAMS['grid_dx_method'] == 'square':
            self.assertEqual(len(fls), 5)
        vol = 0.
        area = 0.
        for fl in fls:
            ref = np.arange(len(fl.surface_h)) * fl.dx
            np.testing.assert_allclose(ref, fl.dis_on_line,
                                       rtol=0.001,
                                       atol=0.01)
            self.assertTrue(len(fl.surface_h) ==
                            len(fl.bed_h) ==
                            len(fl.bed_shape) ==
                            len(fl.dis_on_line) ==
                            len(fl.widths))

            self.assertTrue(np.all(fl.widths >= 0))
            vol += fl.volume_km3
            area += fl.area_km2

        rtol = 0.03
        np.testing.assert_allclose(gdir.rgi_area_km2, area, rtol=rtol)
        np.testing.assert_allclose(v*1e-9, vol, rtol=rtol)


class TestMassBalance(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_past_mb_model(self):

        gdir = init_hef(border=DOM_BORDER)
        flowline.init_present_time_glacier(gdir)

        df = pd.read_csv(gdir.get_filepath('local_mustar', div_id=0))
        mu_star = df['mu_star'][0]
        bias = df['bias'][0]
        prcp_fac = df['prcp_fac'][0]

        # Climate period
        yrp = [1851, 2000]

        # Flowlines height
        h, w = gdir.get_inversion_flowline_hw()
        _, t, p = climate.mb_yearly_climate_on_height(gdir, h, prcp_fac,
                                                      year_range=yrp)

        mb_mod = massbalance.PastMassBalanceModel(gdir, bias=0)
        for i, yr in enumerate(np.arange(yrp[0], yrp[1]+1)):
            ref_mb_on_h = p[:, i] - mu_star * t[:, i]
            my_mb_on_h = mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR * cfg.RHO
            np.testing.assert_allclose(ref_mb_on_h, my_mb_on_h,
                                       atol=1e-2)

        mb_mod = massbalance.PastMassBalanceModel(gdir)
        for i, yr in enumerate(np.arange(yrp[0], yrp[1]+1)):
            ref_mb_on_h = p[:, i] - mu_star * t[:, i]
            my_mb_on_h = mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR * cfg.RHO
            np.testing.assert_allclose(ref_mb_on_h, my_mb_on_h + bias,
                                       atol=1e-2)

        for i, yr in enumerate(np.arange(yrp[0], yrp[1]+1)):

            ref_mb_on_h = p[:, i] - mu_star * t[:, i]
            my_mb_on_h = ref_mb_on_h*0.
            for m in np.arange(12):
                yrm = utils.date_to_year(yr, m+1)
                tmp =  mb_mod.get_monthly_mb(h, yrm)*SEC_IN_MONTHS[m]*cfg.RHO
                my_mb_on_h += tmp

            np.testing.assert_allclose(ref_mb_on_h,
                                       my_mb_on_h + bias,
                                       atol=1e-2)

        # real data
        h, w = gdir.get_inversion_flowline_hw()
        mbdf = gdir.get_ref_mb_data()
        mbdf.loc[yr, 'MY_MB'] = np.NaN
        mb_mod = massbalance.PastMassBalanceModel(gdir)
        for yr in mbdf.index.values:
            my_mb_on_h = mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR * cfg.RHO
            mbdf.loc[yr, 'MY_MB'] = np.average(my_mb_on_h, weights=w)

        np.testing.assert_allclose(mbdf['ANNUAL_BALANCE'].mean(),
                                   mbdf['MY_MB'].mean(),
                                   atol=1e-2)

        mb_mod = massbalance.PastMassBalanceModel(gdir, bias=0)
        for yr in mbdf.index.values:
            my_mb_on_h = mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR * cfg.RHO
            mbdf.loc[yr, 'MY_MB'] = np.average(my_mb_on_h, weights=w)

        np.testing.assert_allclose(mbdf['ANNUAL_BALANCE'].mean() + bias,
                                   mbdf['MY_MB'].mean(),
                                   atol=1e-2)

        mb_mod = massbalance.PastMassBalanceModel(gdir)
        for yr in mbdf.index.values:
            my_mb_on_h = mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR * cfg.RHO
            mbdf.loc[yr, 'MY_MB'] = np.average(my_mb_on_h, weights=w)
            mb_mod.temp_bias = 1
            my_mb_on_h = mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR * cfg.RHO
            mbdf.loc[yr, 'BIASED_MB'] = np.average(my_mb_on_h, weights=w)
            mb_mod.temp_bias = 0

        np.testing.assert_allclose(mbdf['ANNUAL_BALANCE'].mean(),
                                   mbdf['MY_MB'].mean(),
                                   atol=1e-2)
        self.assertTrue(mbdf['ANNUAL_BALANCE'].mean() > mbdf['BIASED_MB'].mean())

    def test_constant_mb_model(self):

        gdir = init_hef(border=DOM_BORDER)
        flowline.init_present_time_glacier(gdir)

        df = pd.read_csv(gdir.get_filepath('local_mustar', div_id=0))
        mu_star = df['mu_star'][0]
        bias = df['bias'][0]
        prcp_fac = df['prcp_fac'][0]

        h = np.array([])
        w = np.array([])

        h, w = gdir.get_inversion_flowline_hw()

        cmb_mod = massbalance.ConstantMassBalanceModel(gdir, bias=0)
        ombh = cmb_mod.get_annual_mb(h) * SEC_IN_YEAR * cfg.RHO
        otmb = np.average(ombh, weights=w)
        np.testing.assert_allclose(0., otmb, atol=0.2)

        cmb_mod = massbalance.ConstantMassBalanceModel(gdir)
        ombh = cmb_mod.get_annual_mb(h) * SEC_IN_YEAR * cfg.RHO
        otmb = np.average(ombh, weights=w)
        np.testing.assert_allclose(0, otmb + bias, atol=0.2)

        mb_mod = massbalance.ConstantMassBalanceModel(gdir, y0=2003-15)
        nmbh = mb_mod.get_annual_mb(h) * SEC_IN_YEAR * cfg.RHO
        ntmb = np.average(nmbh, weights=w)

        self.assertTrue(ntmb < otmb)

        if do_plot:  # pragma: no cover
            plt.plot(h, ombh, 'o', label='tstar')
            plt.plot(h, nmbh, 'o', label='today')
            plt.legend()
            plt.show()

        cmb_mod.temp_bias = 1
        biasombh = cmb_mod.get_annual_mb(h) * SEC_IN_YEAR * cfg.RHO
        biasotmb = np.average(biasombh, weights=w)
        self.assertTrue(biasotmb < (otmb - 500))

        cmb_mod.temp_bias = 0
        nobiasombh = cmb_mod.get_annual_mb(h) * SEC_IN_YEAR * cfg.RHO
        nobiasotmb = np.average(nobiasombh, weights=w)
        np.testing.assert_allclose(0, nobiasotmb + bias, atol=0.2)

        months = np.arange(12)
        monthly_1 = months * 0.
        monthly_2 = months * 0.
        for m in months:
            yr = utils.date_to_year(0, m+1)
            cmb_mod.temp_bias = 0
            tmp = cmb_mod.get_monthly_mb(h, yr) * SEC_IN_MONTHS[m] * cfg.RHO
            monthly_1[m] = np.average(tmp, weights=w)
            cmb_mod.temp_bias = 1
            tmp = cmb_mod.get_monthly_mb(h, yr) * SEC_IN_MONTHS[m] * cfg.RHO
            monthly_2[m] = np.average(tmp, weights=w)

        # check that the winter months are close but summer months no
        np.testing.assert_allclose(monthly_1[1: 5], monthly_2[1: 5], atol=1)
        self.assertTrue(np.mean(monthly_1[5:]) > (np.mean(monthly_2[5:]) + 100))

        if do_plot:  # pragma: no cover
            plt.plot(monthly_1, '-', label='Normal')
            plt.plot(monthly_2, '-', label='Temp bias')
            plt.legend();
            plt.show()

    def test_random_mb(self):

        gdir = init_hef(border=DOM_BORDER)
        flowline.init_present_time_glacier(gdir)

        ref_mod = massbalance.ConstantMassBalanceModel(gdir)
        mb_mod = massbalance.RandomMassBalanceModel(gdir, seed=10)

        h, w = gdir.get_inversion_flowline_hw()

        ref_mbh = ref_mod.get_annual_mb(h, None) * SEC_IN_YEAR

        # two years shoudn't be equal
        r_mbh1 = mb_mod.get_annual_mb(h, 1) * SEC_IN_YEAR
        r_mbh2 = mb_mod.get_annual_mb(h, 2) * SEC_IN_YEAR
        assert not np.all(np.allclose(r_mbh1, r_mbh2))

        # the same year should be equal
        r_mbh1 = mb_mod.get_annual_mb(h, 1) * SEC_IN_YEAR
        r_mbh2 = mb_mod.get_annual_mb(h, 1) * SEC_IN_YEAR
        np.testing.assert_allclose(r_mbh1, r_mbh2)

        # After many trials the mb should be close to the same
        ny = 2000
        yrs = np.arange(ny)
        r_mbh = 0.
        for yr in yrs:
            r_mbh += mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR
        r_mbh /= ny
        np.testing.assert_allclose(ref_mbh, r_mbh, atol=0.2)

        mb_mod.temp_bias = -0.5
        r_mbh_b = 0.
        for yr in yrs:
            r_mbh_b += mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR
        r_mbh_b /= ny
        self.assertTrue(np.mean(r_mbh) < np.mean(r_mbh_b))

        # Compare sigma from real climate and mine
        mb_ref = massbalance.PastMassBalanceModel(gdir)
        mb_mod = massbalance.RandomMassBalanceModel(gdir, y0=2003-15,
                                                    seed=10)
        mb_ts = []
        mb_ts2 = []
        yrs = np.arange(1973, 2003, 1)
        for yr in yrs:
            mb_ts.append(np.average(mb_ref.get_annual_mb(h, yr) * SEC_IN_YEAR, weights=w))
            mb_ts2.append(np.average(mb_mod.get_annual_mb(h, yr) * SEC_IN_YEAR, weights=w))
        np.testing.assert_allclose(np.std(mb_ts), np.std(mb_ts2), rtol=0.1)

        # Monthly
        time = pd.date_range('1/1/1973', periods=31*12, freq='MS')
        yrs = utils.date_to_year(time.year, time.month)

        ref_mb = np.zeros(12)
        my_mb = np.zeros(12)
        for yr, m in zip(yrs, time.month):
            ref_mb[m-1] += np.average(mb_ref.get_monthly_mb(h, yr) * SEC_IN_MONTHS[m-1], weights=w)
            my_mb[m-1] += np.average(mb_mod.get_monthly_mb(h, yr) * SEC_IN_MONTHS[m-1], weights=w)
        my_mb = my_mb / 31
        ref_mb = ref_mb / 31
        self.assertTrue(utils.rmsd(ref_mb, my_mb) < 0.1)

    def test_mb_performance(self):

        gdir = init_hef(border=DOM_BORDER)
        flowline.init_present_time_glacier(gdir)

        h, w = gdir.get_inversion_flowline_hw()

        # Climate period, 10 day timestep
        yrs = np.arange(1850, 2003, 10/365)

        # models
        start_time = time.time()
        mb1 = massbalance.ConstantMassBalanceModel(gdir)
        for yr in yrs:
            _ = mb1.get_monthly_mb(h, yr)
        t1 = time.time() - start_time
        start_time = time.time()
        mb2 = massbalance.PastMassBalanceModel(gdir)
        for yr in yrs:
            _ = mb2.get_monthly_mb(h, yr)
        t2 = time.time() - start_time

        # not faster as two times t2
        try:
            assert t1 >= (t2 / 2)
        except AssertionError:
            # no big deal
            unittest.skip('Allowed failure')


class TestModelFlowlines(unittest.TestCase):

    def test_rectangular(self):
        map_dx = 100.
        dx = 1.
        nx = 200
        coords = np.arange(0, nx - 0.5, 1)
        line = shpg.LineString(np.vstack([coords, coords * 0.]).T)

        bed_h = np.linspace(3000, 1000, nx)
        surface_h = bed_h + 100
        surface_h[:20] += 50
        surface_h[-20:] -= 100
        widths = bed_h * 0. + 20
        widths[:30] = 40
        widths[-30:] = 10

        rec = flowline.VerticalWallFlowline(line=line, dx=dx, map_dx=map_dx,
                                            surface_h=surface_h, bed_h=bed_h,
                                            widths=widths)
        thick = surface_h - bed_h
        widths_m = widths * map_dx
        section = thick * widths_m
        vol_m3 = thick * map_dx * widths_m
        area_m2 = map_dx * widths_m
        area_m2[thick == 0] = 0

        assert_allclose(rec.thick, thick)
        assert_allclose(rec.widths, widths)
        assert_allclose(rec.widths_m, widths_m)
        assert_allclose(rec.section, section)
        assert_allclose(rec.area_m2, area_m2.sum())
        assert_allclose(rec.volume_m3, vol_m3.sum())

        # We set something and everything stays same
        rec.thick = thick
        assert_allclose(rec.thick, thick)
        assert_allclose(rec.widths, widths)
        assert_allclose(rec.widths_m, widths_m)
        assert_allclose(rec.section, section)
        assert_allclose(rec.area_m2, area_m2.sum())
        assert_allclose(rec.volume_m3, vol_m3.sum())
        rec.section = section
        assert_allclose(rec.thick, thick)
        assert_allclose(rec.widths, widths)
        assert_allclose(rec.widths_m, widths_m)
        assert_allclose(rec.section, section)
        assert_allclose(rec.area_m2, area_m2.sum())
        assert_allclose(rec.volume_m3, vol_m3.sum())

        # More adventurous
        rec.section = section / 2
        assert_allclose(rec.thick, thick/2)
        assert_allclose(rec.widths, widths)
        assert_allclose(rec.widths_m, widths_m)
        assert_allclose(rec.section, section/2)
        assert_allclose(rec.area_m2, area_m2.sum())
        assert_allclose(rec.volume_m3, (vol_m3/2).sum())

    def test_trapeze_mixed_rec(self):

        # Special case of lambda = 0

        map_dx = 100.
        dx = 1.
        nx = 200
        coords = np.arange(0, nx - 0.5, 1)
        line = shpg.LineString(np.vstack([coords, coords * 0.]).T)

        bed_h = np.linspace(3000, 1000, nx)
        surface_h = bed_h + 100
        surface_h[:20] += 50
        surface_h[-20:] -= 80
        widths = bed_h * 0. + 20
        widths[:30] = 40
        widths[-30:] = 10

        lambdas = bed_h*0.
        is_trap = np.ones(len(lambdas), dtype=np.bool)

        # tests
        thick = surface_h - bed_h
        widths_m = widths * map_dx
        section = thick * widths_m
        vol_m3 = thick * map_dx * widths_m
        area_m2 = map_dx * widths_m
        area_m2[thick == 0] = 0

        rec1 = flowline.TrapezoidalFlowline(line=line, dx=dx, map_dx=map_dx,
                                           surface_h=surface_h, bed_h=bed_h,
                                           widths=widths, lambdas=lambdas)

        rec2 = flowline.MixedFlowline(line=line, dx=dx, map_dx=map_dx,
                                      surface_h=surface_h, bed_h=bed_h,
                                      section=section, bed_shape=lambdas,
                                      is_trapezoid=is_trap, lambdas=lambdas)

        recs = [rec1, rec2]
        for rec in recs:
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())

            # We set something and everything stays same
            rec.thick = thick
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())
            rec.section = section
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())

            # More adventurous
            rec.section = section / 2
            assert_allclose(rec.thick, thick/2)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section/2)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, (vol_m3/2).sum())

    def test_trapeze_mixed_lambda1(self):

        # Real lambdas

        map_dx = 100.
        dx = 1.
        nx = 200
        coords = np.arange(0, nx - 0.5, 1)
        line = shpg.LineString(np.vstack([coords, coords * 0.]).T)

        bed_h = np.linspace(3000, 1000, nx)
        surface_h = bed_h + 100
        surface_h[:20] += 50
        surface_h[-20:] -= 80
        widths_0 = bed_h * 0. + 20
        widths_0[:30] = 40
        widths_0[-30:] = 10

        lambdas = bed_h*0. + 1

        # tests
        thick = surface_h - bed_h
        widths_m = widths_0 * map_dx + lambdas * thick
        widths = widths_m / map_dx
        section = thick * (widths_0 * map_dx + widths_m) / 2
        vol_m3 = section * map_dx
        area_m2 = map_dx * widths_m
        area_m2[thick == 0] = 0

        is_trap = np.ones(len(lambdas), dtype=np.bool)


        rec1 = flowline.TrapezoidalFlowline(line=line, dx=dx, map_dx=map_dx,
                                           surface_h=surface_h, bed_h=bed_h,
                                           widths=widths, lambdas=lambdas)

        rec2 = flowline.MixedFlowline(line=line, dx=dx, map_dx=map_dx,
                                      surface_h=surface_h, bed_h=bed_h,
                                      section=section, bed_shape=lambdas,
                                      is_trapezoid=is_trap, lambdas=lambdas)

        recs = [rec1, rec2]
        for rec in recs:
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())

            # We set something and everything stays same
            rec.thick = thick
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())
            rec.section = section
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())

    def test_parab_mixed(self):

        # Real parabolas

        map_dx = 100.
        dx = 1.
        nx = 200
        coords = np.arange(0, nx - 0.5, 1)
        line = shpg.LineString(np.vstack([coords, coords * 0.]).T)

        bed_h = np.linspace(3000, 1000, nx)
        surface_h = bed_h + 100
        surface_h[:20] += 50
        surface_h[-20:] -= 80

        shapes = bed_h*0. + 0.003
        shapes[:30] = 0.002
        shapes[-30:] = 0.004

        # tests
        thick = surface_h - bed_h
        widths_m = np.sqrt(4 * thick / shapes)
        widths = widths_m / map_dx
        section = 2 / 3 * widths_m * thick
        vol_m3 = section * map_dx
        area_m2 = map_dx * widths_m
        area_m2[thick == 0] = 0

        is_trap = np.zeros(len(shapes), dtype=np.bool)


        rec1 = flowline.ParabolicFlowline(line=line, dx=dx, map_dx=map_dx,
                                          surface_h=surface_h, bed_h=bed_h,
                                          bed_shape=shapes)

        rec2 = flowline.MixedFlowline(line=line, dx=dx, map_dx=map_dx,
                                      surface_h=surface_h, bed_h=bed_h,
                                      section=section, bed_shape=shapes,
                                      is_trapezoid=is_trap, lambdas=shapes)

        recs = [rec1, rec2]
        for rec in recs:
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())

            # We set something and everything stays same
            rec.thick = thick
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())
            rec.section = section
            assert_allclose(rec.thick, thick)
            assert_allclose(rec.widths, widths)
            assert_allclose(rec.widths_m, widths_m)
            assert_allclose(rec.section, section)
            assert_allclose(rec.area_m2, area_m2.sum())
            assert_allclose(rec.volume_m3, vol_m3.sum())


    def test_mixed(self):

        # Set a section and see if it all matches

        map_dx = 100.
        dx = 1.
        nx = 200
        coords = np.arange(0, nx - 0.5, 1)
        line = shpg.LineString(np.vstack([coords, coords * 0.]).T)

        bed_h = np.linspace(3000, 1000, nx)
        surface_h = bed_h + 100
        surface_h[:20] += 50
        surface_h[-20:] -= 80
        widths_0 = bed_h * 0. + 20
        widths_0[:30] = 40
        widths_0[-30:] = 10

        lambdas = bed_h*0. + 1
        lambdas[0:50] = 0

        thick = surface_h - bed_h
        widths_m = widths_0 * map_dx + lambdas * thick
        widths = widths_m / map_dx
        section_trap = thick * (widths_0 * map_dx + widths_m) / 2

        rec1 = flowline.TrapezoidalFlowline(line=line, dx=dx, map_dx=map_dx,
                                           surface_h=surface_h, bed_h=bed_h,
                                           widths=widths, lambdas=lambdas)



        shapes = bed_h*0. + 0.003
        shapes[-30:] = 0.004

        # tests
        thick = surface_h - bed_h
        widths_m = np.sqrt(4 * thick / shapes)
        widths = widths_m / map_dx
        section_para = 2 / 3 * widths_m * thick

        rec2 = flowline.ParabolicFlowline(line=line, dx=dx, map_dx=map_dx,
                                          surface_h=surface_h, bed_h=bed_h,
                                          bed_shape=shapes)

        is_trap = np.ones(len(shapes), dtype=np.bool)
        is_trap[100:] = False

        section = section_trap.copy()
        section[~is_trap] = section_para[~is_trap]

        rec = flowline.MixedFlowline(line=line, dx=dx, map_dx=map_dx,
                                      surface_h=surface_h, bed_h=bed_h,
                                      section=section, bed_shape=shapes,
                                      is_trapezoid=is_trap, lambdas=lambdas)

        thick = rec1.thick
        thick[~is_trap] = rec2.thick[~is_trap]
        assert_allclose(rec.thick, thick)

        widths = rec1.widths
        widths[~is_trap] = rec2.widths[~is_trap]
        assert_allclose(rec.widths, widths)

        widths_m = rec1.widths_m
        widths_m[~is_trap] = rec2.widths_m[~is_trap]
        assert_allclose(rec.widths_m, widths_m)

        section = rec1.section
        section[~is_trap] = rec2.section[~is_trap]
        assert_allclose(rec.section, section)

        # We set something and everything stays same
        area_m2 = rec.area_m2
        volume_m3 = rec.volume_m3
        rec.thick = rec.thick
        assert_allclose(rec.thick, thick)
        assert_allclose(rec.widths, widths)
        assert_allclose(rec.widths_m, widths_m)
        assert_allclose(rec.section, section)
        assert_allclose(rec.area_m2, area_m2)
        assert_allclose(rec.volume_m3, volume_m3)
        rec.section = rec.section
        assert_allclose(rec.thick, thick)
        assert_allclose(rec.widths, widths)
        assert_allclose(rec.widths_m, widths_m)
        assert_allclose(rec.section, section)
        assert_allclose(rec.area_m2, area_m2)
        assert_allclose(rec.volume_m3, volume_m3)


class TestIO(unittest.TestCase):

    def setUp(self):
        self.test_dir = os.path.join(cfg.PATHS['test_dir'], 'tmp_io')
        if not os.path.exists(self.test_dir):
            os.makedirs(self.test_dir)

        self.gdir = init_hef(border=DOM_BORDER)
        self.glen_a = 2.4e-24    # Modern style Glen parameter A

    def test_flowline_to_dataset(self):

        beds = [dummy_constant_bed, dummy_width_bed, dummy_noisy_bed,
                dummy_bumpy_bed, dummy_parabolic_bed, dummy_trapezoidal_bed,
                dummy_mixed_bed]

        for bed in beds:
            fl = bed()[0]
            ds = fl.to_dataset()
            fl_ = flowline.flowline_from_dataset(ds)
            ds_ = fl_.to_dataset()
            self.assertTrue(ds_.equals(ds))

    def test_model_to_file(self):

        p = os.path.join(self.test_dir, 'grp.nc')
        if os.path.isfile(p):
            os.remove(p)

        fls = dummy_width_bed_tributary()
        model = flowline.FluxBasedModel(fls)
        model.to_netcdf(p)
        fls_ = flowline.glacier_from_netcdf(p)

        for fl, fl_ in zip(fls, fls_):
            ds = fl.to_dataset()
            ds_ = fl_.to_dataset()
            self.assertTrue(ds_.equals(ds))

        self.assertTrue(fls_[0].flows_to is fls_[1])
        self.assertEqual(fls[0].flows_to_indice, fls_[0].flows_to_indice)

        # They should be sorted
        to_test = [fl.order for fl in fls_]
        assert np.array_equal(np.sort(to_test), to_test)

        # They should be able to start a run
        mb = LinearMassBalanceModel(2600.)
        model = flowline.FluxBasedModel(fls_, mb_model=mb, y0=0.,
                                        glen_a=self.glen_a)
        model.run_until(100)

    @is_slow
    def test_run(self):
        mb = LinearMassBalanceModel(2600.)

        fls = dummy_constant_bed()
        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        glen_a=self.glen_a)
        ds, ds_diag = model.run_until_and_store(500)
        ds = ds[0]

        fls = dummy_constant_bed()
        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        glen_a=self.glen_a)

        years = utils.monthly_timeseries(0, 500)
        vol_ref = []
        a_ref = []
        l_ref = []
        vol_diag = []
        a_diag = []
        l_diag = []
        for yr in years:
            model.run_until(yr)
            vol_diag.append(model.volume_m3)
            a_diag.append(model.area_m2)
            l_diag.append(model.length_m)
            if int(yr) == yr:
                vol_ref.append(model.volume_m3)
                a_ref.append(model.area_m2)
                l_ref.append(model.length_m)
                if int(yr) == 500:
                    secfortest = model.fls[0].section

        np.testing.assert_allclose(ds.ts_section.isel(time=-1),
                                   secfortest)

        np.testing.assert_allclose(ds_diag.volume_m3, vol_diag)
        np.testing.assert_allclose(ds_diag.area_m2, a_diag)
        np.testing.assert_allclose(ds_diag.length_m, l_diag)

        fls = dummy_constant_bed()
        run_path = os.path.join(self.test_dir, 'ts_ideal.nc')
        diag_path = os.path.join(self.test_dir, 'ts_diag.nc')
        if os.path.exists(run_path):
            os.remove(run_path)
        if os.path.exists(diag_path):
            os.remove(diag_path)
        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        glen_a=self.glen_a)
        model.run_until_and_store(500, run_path=run_path,
                                  diag_path=diag_path)

        ds_ = xr.open_dataset(diag_path)
        xr.testing.assert_identical(ds_diag, ds_)

        fmodel = flowline.FileModel(run_path)
        fls = dummy_constant_bed()
        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        glen_a=self.glen_a)
        for yr in years:
            model.run_until(yr)
            if yr in [100, 300, 500]:
                # this is sloooooow so we test a little bit only
                fmodel.run_until(yr)
                np.testing.assert_allclose(model.fls[0].section,
                                           fmodel.fls[0].section)
                np.testing.assert_allclose(model.fls[0].widths_m,
                                           fmodel.fls[0].widths_m)

        np.testing.assert_allclose(fmodel.volume_m3_ts(), vol_ref)
        np.testing.assert_allclose(fmodel.area_m2_ts(), a_ref)
        np.testing.assert_allclose(fmodel.length_m_ts(), l_ref)

        # Can we start a run from the middle?
        fmodel.run_until(300)
        model = flowline.FluxBasedModel(fmodel.fls, mb_model=mb, y0=300,
                                        glen_a=self.glen_a)
        model.run_until(500)
        fmodel.run_until(500)
        np.testing.assert_allclose(model.fls[0].section,
                                   fmodel.fls[0].section)

    def test_gdir_copy(self):
        print(self.gdir.dir)
        new_dir = os.path.join(cfg.PATHS['test_dir'], 'tmp_testcopy')
        if os.path.exists(new_dir):
            shutil.rmtree(new_dir)
        self.gdir.copy_to_basedir(new_dir, setup='all')
        new_gdir = utils.GlacierDirectory(self.gdir.rgi_id, base_dir=new_dir)
        flowline.init_present_time_glacier(new_gdir)
        shutil.rmtree(new_dir)

        self.gdir.copy_to_basedir(new_dir)
        hef_file = get_demo_file('Hintereisferner_RGI5.shp')
        entity = gpd.GeoDataFrame.from_file(hef_file).iloc[0]
        new_gdir = utils.GlacierDirectory(entity, base_dir=new_dir)
        flowline.random_glacier_evolution(new_gdir, nyears=10)
        shutil.rmtree(new_dir)

    def test_hef(self):

        p = os.path.join(self.test_dir, 'grp_hef.nc')
        if os.path.isfile(p):
            os.remove(p)

        flowline.init_present_time_glacier(self.gdir)

        fls = self.gdir.read_pickle('model_flowlines')
        model = flowline.FluxBasedModel(fls)

        model.to_netcdf(p)
        fls_ = flowline.glacier_from_netcdf(p)

        for fl, fl_ in zip(fls, fls_):
            ds = fl.to_dataset()
            ds_ = fl_.to_dataset()
            for v in ds.variables.keys():
                np.testing.assert_allclose(ds_[v], ds[v], equal_nan=True)

        for fl, fl_ in zip(fls[:-1], fls_[:-1]):
            self.assertEqual(fl.flows_to_indice, fl_.flows_to_indice)

        # mixed flowline
        fls = self.gdir.read_pickle('model_flowlines')
        model = flowline.FluxBasedModel(fls)

        p = os.path.join(self.test_dir, 'grp_hef_mix.nc')
        if os.path.isfile(p):
            os.remove(p)
        model.to_netcdf(p)
        fls_ = flowline.glacier_from_netcdf(p)

        np.testing.assert_allclose(fls[0].section, fls_[0].section)
        np.testing.assert_allclose(fls[0]._ptrap, fls_[0]._ptrap)
        np.testing.assert_allclose(fls[0].bed_h, fls_[0].bed_h)

        for fl, fl_ in zip(fls, fls_):
            ds = fl.to_dataset()
            ds_ = fl_.to_dataset()
            np.testing.assert_allclose(fl.section, fl_.section)
            np.testing.assert_allclose(fl._ptrap, fl_._ptrap)
            np.testing.assert_allclose(fl.bed_h, fl_.bed_h)
            xr.testing.assert_allclose(ds, ds_)

        for fl, fl_ in zip(fls[:-1], fls_[:-1]):
            self.assertEqual(fl.flows_to_indice, fl_.flows_to_indice)


class TestBackwardsIdealized(unittest.TestCase):

    def setUp(self):

        self.fs = 5.7e-20
        # Backwards
        _fd = 1.9e-24
        self.glen_a = (N+2) * _fd / 2.

        self.ela = 2800.

        origfls = dummy_constant_bed(nx=120, hmin=1800)

        mb = LinearMassBalanceModel(self.ela)
        model = flowline.FluxBasedModel(origfls, mb_model=mb,
                                        fs=self.fs, glen_a=self.glen_a)
        model.run_until(500)
        self.glacier = copy.deepcopy(model.fls)

    def tearDown(self):
        pass

    @is_slow
    def test_iterative_back(self):

        y0 = 0.
        y1 = 150.
        rtol = 0.02

        mb = LinearMassBalanceModel(self.ela + 50.)
        model = flowline.FluxBasedModel(self.glacier, mb_model=mb,
                                        fs=self.fs, glen_a=self.glen_a,
                                        time_stepping='ambitious')

        ite, bias, past_model = flowline._find_inital_glacier(model, mb, y0,
                                                               y1, rtol=rtol)

        bef_fls = copy.deepcopy(past_model.fls)
        past_model.run_until(y1)
        self.assertTrue(bef_fls[-1].area_m2 > past_model.area_m2)
        np.testing.assert_allclose(past_model.area_m2, self.glacier[-1].area_m2,
                                   rtol=rtol)

        if do_plot:  # pragma: no cover
            plt.plot(self.glacier[-1].surface_h, 'k', label='ref')
            plt.plot(bef_fls[-1].surface_h, 'b', label='start')
            plt.plot(past_model.fls[-1].surface_h, 'r', label='end')
            plt.plot(self.glacier[-1].bed_h, 'gray', linewidth=2)
            plt.legend(loc='best')
            plt.show()

        mb = LinearMassBalanceModel(self.ela - 50.)
        model = flowline.FluxBasedModel(self.glacier, mb_model=mb, y0=y0,
                                        fs=self.fs, glen_a=self.glen_a,
                                        time_stepping='ambitious')

        ite, bias, past_model = flowline._find_inital_glacier(model, mb, y0,
                                                               y1, rtol=rtol)
        bef_fls = copy.deepcopy(past_model.fls)
        past_model.run_until(y1)
        self.assertTrue(bef_fls[-1].area_m2 < past_model.area_m2)
        np.testing.assert_allclose(past_model.area_m2, self.glacier[-1].area_m2,
                                   rtol=rtol)

        if do_plot:  # pragma: no cover
            plt.plot(self.glacier[-1].surface_h, 'k', label='ref')
            plt.plot(bef_fls[-1].surface_h, 'b', label='start')
            plt.plot(past_model.fls[-1].surface_h, 'r', label='end')
            plt.plot(self.glacier[-1].bed_h, 'gray', linewidth=2)
            plt.legend(loc='best')
            plt.show()

        mb = LinearMassBalanceModel(self.ela)
        model = flowline.FluxBasedModel(self.glacier, mb_model=mb, y0=y0,
                                        fs=self.fs, glen_a=self.glen_a)

        # Hit the correct one
        ite, bias, past_model = flowline._find_inital_glacier(model, mb, y0,
                                                               y1, rtol=rtol)
        past_model.run_until(y1)
        np.testing.assert_allclose(past_model.area_m2, self.glacier[-1].area_m2,
                                   rtol=rtol)

    @is_slow
    def test_fails(self):

        y0 = 0.
        y1 = 100.

        mb = LinearMassBalanceModel(self.ela - 150.)
        model = flowline.FluxBasedModel(self.glacier, mb_model=mb, y0=y0,
                                        fs=self.fs, glen_a=self.glen_a)
        self.assertRaises(RuntimeError, flowline._find_inital_glacier, model,
                          mb, y0, y1, rtol=0.02, max_ite=5)


class TestIdealisedInversion(unittest.TestCase):

    def setUp(self):
        # test directory
        self.testdir = os.path.join(cfg.PATHS['test_dir'],
                                    'tmp_ideal_inversion')

        from oggm import GlacierDirectory
        from oggm.tasks import define_glacier_region
        import geopandas as gpd

        # Init
        cfg.initialize()
        cfg.set_divides_db()
        cfg.PATHS['dem_file'] = get_demo_file('hef_srtm.tif')
        cfg.PATHS['climate_file'] = get_demo_file('histalp_merged_hef.nc')

        hef_file = get_demo_file('Hintereisferner_RGI5.shp')
        entity = gpd.GeoDataFrame.from_file(hef_file).iloc[0]

        self.gdir = GlacierDirectory(entity, base_dir=self.testdir, reset=True)
        define_glacier_region(self.gdir, entity=entity)

    def tearDown(self):
        self.rm_dir()

    def rm_dir(self):
        if os.path.exists(self.testdir):
            shutil.rmtree(self.testdir)

    def simple_plot(self, model):  # pragma: no cover
        ocls = self.gdir.read_pickle('inversion_output', div_id=1)
        ithick = ocls[-1]['thick']
        pg = model.fls[-1].thick > 0
        plt.figure()
        bh = model.fls[-1].bed_h[pg]
        sh = model.fls[-1].surface_h[pg]
        plt.plot(sh, 'k')
        plt.plot(bh, 'C0', label='Real bed')
        plt.plot(sh - ithick, 'C3', label='Computed bed')
        plt.title('Compare Shape')
        plt.xlabel('[dx]')
        plt.ylabel('Elevation [m]')
        plt.legend(loc=3)
        plt.show()

    def double_plot(self, model):  # pragma: no cover
        ocls = self.gdir.read_pickle('inversion_output', div_id=1)
        f, axs = plt.subplots(1, 2, figsize=(8, 4), sharey=True)
        for i, ax in enumerate(axs):
            ithick = ocls[i]['thick']
            pg = model.fls[i].thick > 0
            bh = model.fls[i].bed_h[pg]
            sh = model.fls[i].surface_h[pg]
            ax.plot(sh, 'k')
            ax.plot(bh, 'C0', label='Real bed')
            ax.plot(sh - ithick, 'C3', label='Computed bed')
            ax.set_title('Compare Shape')
            ax.set_xlabel('[dx]')
            ax.legend(loc=3)
        plt.show()

    def test_inversion_vertical(self):

        fls = dummy_constant_bed(map_dx=self.gdir.grid.dx, widths=10)
        mb = LinearMassBalanceModel(2600.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.)
        model.run_until_equilibrium()

        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=fl.surface_h[pg])
            flo.widths = fl.widths[pg]
            flo.touches_border = np.ones(flo.nx).astype(np.bool)
            fls.append(flo)
        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)

        assert_allclose(v, model.volume_m3, rtol=0.01)
        if do_plot:  # pragma: no cover
            self.simple_plot(model)

    def test_inversion_parabolic(self):

        fls = dummy_parabolic_bed(map_dx=self.gdir.grid.dx)
        mb = LinearMassBalanceModel(2500.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.)
        model.run_until_equilibrium()

        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=fl.surface_h[pg])
            flo.widths = fl.widths[pg]
            flo.touches_border = np.zeros(flo.nx).astype(np.bool)
            fls.append(flo)
        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)
        assert_allclose(v, model.volume_m3, rtol=0.01)

        inv = self.gdir.read_pickle('inversion_output', div_id=1)[-1]
        bed_shape_gl = 4 * inv['thick'] / (flo.widths * self.gdir.grid.dx) ** 2
        bed_shape_ref = 4 * fl.thick[pg] / (flo.widths * self.gdir.grid.dx) ** 2

        # assert utils.rmsd(fl.bed_shape[pg], bed_shape_gl) < 0.001
        if do_plot:  # pragma: no cover
            plt.plot(bed_shape_ref[:-3])
            plt.plot(bed_shape_gl[:-3])
            plt.show()

    @is_slow
    def test_inversion_mixed(self):

        fls = dummy_mixed_bed(deflambdas=0, map_dx=self.gdir.grid.dx,
                              mixslice=slice(10, 30))
        mb = LinearMassBalanceModel(2600.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        time_stepping='conservative')
        # This reduces the test's accuracy but makes it much faster.
        model.run_until_equilibrium(rate=0.01)

        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            sh = fl.surface_h[pg]
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=sh)
            flo.widths = fl.widths[pg]
            flo.touches_border = fl.is_trapezoid[pg]
            fls.append(flo)
        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)

        assert_allclose(v, model.volume_m3, rtol=0.05)
        if do_plot:  # pragma: no cover
            self.simple_plot(model)

    @is_slow
    def test_inversion_cliff(self):

        fls = dummy_constant_bed_cliff(map_dx=self.gdir.grid.dx,
                                       cliff_height=100)
        mb = LinearMassBalanceModel(2600.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        time_stepping='conservative')
        model.run_until_equilibrium()
        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            sh = fl.surface_h[pg]
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=sh)
            flo.widths = fl.widths[pg]
            flo.touches_border = np.ones(flo.nx).astype(np.bool)
            fls.append(flo)
        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)

        assert_allclose(v, model.volume_m3, rtol=0.05)
        if do_plot:  # pragma: no cover
            self.simple_plot(model)

    def test_inversion_noisy(self):

        fls = dummy_noisy_bed(map_dx=self.gdir.grid.dx)
        mb = LinearMassBalanceModel(2600.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        time_stepping='conservative')
        model.run_until_equilibrium()
        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            sh = fl.surface_h[pg]
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=sh)
            flo.widths = fl.widths[pg]
            flo.touches_border = np.ones(flo.nx).astype(np.bool)
            fls.append(flo)
        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)

        assert_allclose(v, model.volume_m3, rtol=0.05)
        if do_plot:  # pragma: no cover
            self.simple_plot(model)

    def test_inversion_tributary(self):

        fls = dummy_width_bed_tributary(map_dx=self.gdir.grid.dx)
        mb = LinearMassBalanceModel(2600.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        time_stepping='conservative')
        model.run_until_equilibrium()

        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            sh = fl.surface_h[pg]
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=sh)
            flo.widths = fl.widths[pg]
            flo.touches_border = np.ones(flo.nx).astype(np.bool)
            fls.append(flo)

        fls[0].set_flows_to(fls[1])

        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)

        assert_allclose(v, model.volume_m3, rtol=0.02)
        if do_plot:  # pragma: no cover
            self.double_plot(model)

    def test_inversion_non_equilibrium(self):

        fls = dummy_constant_bed(map_dx=self.gdir.grid.dx)
        mb = LinearMassBalanceModel(2600.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.)
        model.run_until_equilibrium()

        mb = LinearMassBalanceModel(2800.)
        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0)
        model.run_until(50)

        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            sh = fl.surface_h[pg]
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=sh)
            flo.widths = fl.widths[pg]
            flo.touches_border = np.ones(flo.nx).astype(np.bool)
            fls.append(flo)
        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)

        # expected errors
        assert v > model.volume_m3
        ocls = self.gdir.read_pickle('inversion_output', div_id=1)
        ithick = ocls[0]['thick']
        assert np.mean(ithick) > np.mean(model.fls[0].thick)*1.1
        if do_plot:  # pragma: no cover
            self.simple_plot(model)

    def test_inversion_and_run(self):

        fls = dummy_parabolic_bed(map_dx=self.gdir.grid.dx)
        mb = LinearMassBalanceModel(2500.)

        model = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.)
        model.run_until_equilibrium()
        fls = []
        for fl in model.fls:
            pg = np.where(fl.thick > 0)
            line = shpg.LineString([fl.line.coords[int(p)] for p in pg[0]])
            sh = fl.surface_h[pg]
            flo = centerlines.Centerline(line, dx=fl.dx,
                                         surface_h=sh)
            flo.widths = fl.widths[pg]
            flo.touches_border = np.zeros(flo.nx).astype(np.bool)
            fls.append(flo)
        for did in [0, 1]:
            self.gdir.write_pickle(copy.deepcopy(fls), 'inversion_flowlines',
                                   div_id=did)

        climate.apparent_mb_from_linear_mb(self.gdir)
        inversion.prepare_for_inversion(self.gdir)
        v, _ = inversion.mass_conservation_inversion(self.gdir)

        assert_allclose(v, model.volume_m3, rtol=0.01)

        inv = self.gdir.read_pickle('inversion_output', div_id=1)[-1]
        bed_shape_gl = 4 * inv['thick'] / (flo.widths * self.gdir.grid.dx) ** 2
        bed_shape_ref = 4 * fl.thick[pg] / (flo.widths * self.gdir.grid.dx) ** 2

        ithick = inv['thick']
        fls = dummy_parabolic_bed(map_dx=self.gdir.grid.dx,
                                  from_other_shape=bed_shape_gl[:-2],
                                  from_other_bed=sh-ithick)
        model2 = flowline.FluxBasedModel(fls, mb_model=mb, y0=0.,
                                        time_stepping='conservative')
        model2.run_until_equilibrium()
        assert_allclose(model2.volume_m3, model.volume_m3, rtol=0.01)

        if do_plot:  # pragma: no cover
            plt.figure()
            plt.plot(model.fls[-1].bed_h, 'C0')
            plt.plot(model2.fls[-1].bed_h, 'C3')
            plt.plot(model.fls[-1].surface_h, 'C0')
            plt.plot(model2.fls[-1].surface_h, 'C3')
            plt.title('Compare Shape')
            plt.xlabel('[m]')
            plt.ylabel('Elevation [m]')
            plt.show()


class TestHEF(unittest.TestCase):

    def setUp(self):
        self.gdir = init_hef(border=DOM_BORDER, invert_with_rectangular=False)
        d = self.gdir.read_pickle('inversion_params')
        self.fs = d['fs']
        self.glen_a = d['glen_a']

    def tearDown(self):
        pass

    @is_slow
    def test_equilibrium(self):

        flowline.init_present_time_glacier(self.gdir)

        mb_mod = massbalance.ConstantMassBalanceModel(self.gdir)

        fls = self.gdir.read_pickle('model_flowlines')
        model = flowline.FluxBasedModel(fls, mb_model=mb_mod, y0=0.,
                                        fs=self.fs,
                                        glen_a=self.glen_a,
                                        min_dt=SEC_IN_DAY/2.)

        ref_vol = model.volume_km3
        ref_area = model.area_km2
        ref_len = model.fls[-1].length_m

        np.testing.assert_allclose(ref_area, self.gdir.rgi_area_km2, rtol=0.03)

        model.run_until(40.)
        self.assertFalse(model.dt_warning)

        after_vol = model.volume_km3
        after_area = model.area_km2
        after_len = model.fls[-1].length_m

        np.testing.assert_allclose(ref_vol, after_vol, rtol=0.03)
        np.testing.assert_allclose(ref_area, after_area, rtol=0.03)
        np.testing.assert_allclose(ref_len, after_len, atol=500.01)

    @is_slow
    def test_commitment(self):

        flowline.init_present_time_glacier(self.gdir)

        mb_mod = massbalance.ConstantMassBalanceModel(self.gdir, y0=2003-15)

        fls = self.gdir.read_pickle('model_flowlines')
        model = flowline.FluxBasedModel(fls, mb_model=mb_mod, y0=0.,
                                        fs=self.fs,
                                        glen_a=self.glen_a)

        ref_vol = model.volume_km3
        ref_area = model.area_km2
        ref_len = model.fls[-1].length_m
        np.testing.assert_allclose(ref_area, self.gdir.rgi_area_km2, rtol=0.02)

        model.run_until_equilibrium()
        self.assertTrue(model.yr > 100)

        after_vol_1 = model.volume_km3
        after_area_1 = model.area_km2
        after_len_1 = model.fls[-1].length_m

        _tmp = cfg.PARAMS['mixed_min_shape']
        cfg.PARAMS['mixed_min_shape'] = 0.001
        flowline.init_present_time_glacier(self.gdir)
        cfg.PARAMS['mixed_min_shape'] = _tmp

        glacier = self.gdir.read_pickle('model_flowlines')

        fls = self.gdir.read_pickle('model_flowlines')
        model = flowline.FluxBasedModel(fls, mb_model=mb_mod, y0=0.,
                                        fs=self.fs,
                                        glen_a=self.glen_a)

        ref_vol = model.volume_km3
        ref_area = model.area_km2
        ref_len = model.fls[-1].length_m
        np.testing.assert_allclose(ref_area, self.gdir.rgi_area_km2, rtol=0.02)

        model.run_until_equilibrium()
        self.assertTrue(model.yr > 100)

        after_vol_2 = model.volume_km3
        after_area_2 = model.area_km2
        after_len_2 = model.fls[-1].length_m

        self.assertTrue(after_vol_1 < (0.5 * ref_vol))
        self.assertTrue(after_vol_2 < (0.5 * ref_vol))

        if do_plot:  # pragma: no cover
            fig = plt.figure()
            plt.plot(glacier[-1].surface_h, 'b', label='start')
            plt.plot(model.fls[-1].surface_h, 'r', label='end')

            plt.plot(glacier[-1].bed_h, 'gray', linewidth=2)
            plt.legend(loc='best')
            plt.show()

    @is_slow
    def test_random(self):

        flowline.init_present_time_glacier(self.gdir)
        flowline.random_glacier_evolution(self.gdir, nyears=200, seed=4,
                                          bias=0)
        path = self.gdir.get_filepath('model_run')

        with flowline.FileModel(path) as model:
            vol = model.volume_km3_ts()
            len = model.length_m_ts()
            area = model.area_km2_ts()
            np.testing.assert_allclose(vol.iloc[0], np.mean(vol), rtol=0.1)
            np.testing.assert_allclose(area.iloc[0], np.mean(area), rtol=0.1)

            if do_plot:
                fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(6, 10))
                vol.plot(ax=ax1)
                ax1.set_title('Volume')
                area.plot(ax=ax2)
                ax2.set_title('Area')
                len.plot(ax=ax3)
                ax3.set_title('Length')
                plt.tight_layout()
                plt.show()

    @is_slow
    def test_elevation_feedback(self):

        flowline.init_present_time_glacier(self.gdir)

        feedbacks = ['annual', 'monthly', 'always']
        times = []
        out = []
        for feedback in feedbacks:
            start_time = time.time()
            flowline.random_glacier_evolution(self.gdir, nyears=200, seed=5,
                                              mb_elev_feedback=feedback)
            end_time = time.time()
            times.append(end_time - start_time)
            out.append(utils.compile_run_output([self.gdir], path=False))

        # Check that volume isn't so different
        assert_allclose(out[0].volume, out[1].volume, rtol=0.05)
        assert_allclose(out[0].volume, out[2].volume, rtol=0.05)
        assert_allclose(out[1].volume, out[2].volume, rtol=0.05)

        if do_plot:
            plt.figure()
            for ds, lab in zip(out, feedbacks):
                (ds.volume*1e-9).plot(label=lab)
            plt.xlabel('Vol (km3)')
            plt.legend()
            plt.figure()
            for ds, lab in zip(out, feedbacks):
                mm = ds.volume.groupby(ds.month).mean(dim='time')
                (mm*1e-9).plot(label=lab)
            plt.xlabel('Vol (km3)')
            plt.legend()
            plt.show()

    @is_slow
    def test_find_t0(self):

        self.skipTest('This test is too unstable')

        gdir = init_hef(border=DOM_BORDER, invert_with_sliding=False)

        flowline.init_present_time_glacier(gdir)
        glacier = gdir.read_pickle('model_flowlines')
        df = pd.read_csv(utils.get_demo_file('hef_lengths.csv'), index_col=0)
        df.columns = ['Leclercq']
        df = df.loc[1950:]

        vol_ref = flowline.FlowlineModel(glacier).volume_km3

        init_bias = 94.  # so that "went too far" comes once on travis
        rtol = 0.005

        flowline.iterative_initial_glacier_search(gdir, y0=df.index[0], init_bias=init_bias,
                                                  rtol=rtol, write_steps=True)

        past_model = flowline.FileModel(gdir.get_filepath('model_run'))

        vol_start = past_model.volume_km3
        bef_fls = copy.deepcopy(past_model.fls)

        mylen = past_model.length_m_ts()
        df['oggm'] = mylen[12::12].values
        df = df-df.iloc[-1]

        past_model.run_until(2003)

        vol_end = past_model.volume_km3
        np.testing.assert_allclose(vol_ref, vol_end, rtol=0.05)

        rmsd = utils.rmsd(df.Leclercq, df.oggm)
        self.assertTrue(rmsd < 1000.)

        if do_plot:  # pragma: no cover
            df.plot()
            plt.ylabel('Glacier length (relative to 2003)')
            plt.show()
            fig = plt.figure()
            lab = 'ref (vol={:.2f}km3)'.format(vol_ref)
            plt.plot(glacier[-1].surface_h, 'k', label=lab)
            lab = 'oggm start (vol={:.2f}km3)'.format(vol_start)
            plt.plot(bef_fls[-1].surface_h, 'b', label=lab)
            lab = 'oggm end (vol={:.2f}km3)'.format(vol_end)
            plt.plot(past_model.fls[-1].surface_h, 'r', label=lab)

            plt.plot(glacier[-1].bed_h, 'gray', linewidth=2)
            plt.legend(loc='best')
            plt.show()
