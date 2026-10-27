from datetime import datetime as RealDatetime
from unittest.mock import Mock, patch
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import whatsapp_funnel as ui
from test_funnel import example_report

class FrozenDatetime(RealDatetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat('2026-10-27T20:00:00-04:00')

class DisplayTest(unittest.TestCase):
    def run_view(self,start,contacts=None):
        st=Mock()
        availability={'capture_since':'2026-10-27T23:19:00+00:00','contacts':contacts or []}
        with patch.object(ui,'datetime',FrozenDatetime),patch.object(ui,'fetch_live_contacts',return_value=availability),patch.object(ui,'fetch_funnel_report',return_value=example_report()) as historical:
            ui.render_whatsapp_funnel(st,lambda key:'https://example.com' if key.endswith('URL') else 'test',start,RealDatetime(2026,10,27).date(),api=Mock())
        return st,historical

    def test_partial_month_keeps_history_instead_of_replacing_it_with_zeros(self):
        st,historical=self.run_view(RealDatetime(2026,10,1).date())
        historical.assert_called_once_with('https://example.com','test','2026-10-01','2026-10-26')
        self.assertEqual(st.dataframe.call_count,1)
        self.assertGreater(st.dataframe.call_args.args[0][0]['People'],0)
        st.cache_data.assert_not_called()
        st.radio.assert_not_called()
        self.assertIn('not reconciled',st.warning.call_args.args[0])

    def test_new_activity_is_not_blindly_added_to_an_anonymous_history(self):
        st,_=self.run_view(RealDatetime(2026,10,1).date(),contacts=[{'key':'test-only'}])
        self.assertEqual(st.dataframe.call_count,1)
        self.assertEqual(st.dataframe.call_args.args[0][0]['People'],example_report()['counts']['contacts'])
        self.assertIn('duplicates',st.warning.call_args.args[0])

    def test_empty_live_day_does_not_show_a_full_day_zero_funnel(self):
        st,historical=self.run_view(RealDatetime(2026,10,27).date())
        historical.assert_not_called();st.dataframe.assert_not_called()
        self.assertIn('No incoming activity',st.info.call_args.args[0])

if __name__=='__main__':unittest.main()
