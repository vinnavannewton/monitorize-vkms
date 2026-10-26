"""The XRandR lifecycle is a server capability, not a Cinnamon-specific route."""
import os
import unittest
from unittest.mock import patch
from monitorize_vkms import compositor, helper


class GenericX11Tests(unittest.TestCase):
    def test_xorg_desktops_use_the_same_activation_snapshot_and_removal(self):
        for name in ('XFCE', 'MATE', 'LXQt', 'Openbox', 'unknown', 'KDE', 'GNOME', 'X-Cinnamon'):
            with self.subTest(desktop=name),patch.dict(os.environ,{'XDG_SESSION_TYPE':'x11','DISPLAY':':0','XDG_CURRENT_DESKTOP':name},clear=True):
                desktop=compositor.detect_compositor()
                helper._validate_compositor_support(desktop,dict(os.environ))
                with patch.object(compositor,'xrandr_outputs',return_value=['baseline']):
                    self.assertEqual(compositor.get_compositor_snapshot(),(desktop,['baseline']))
                with patch.object(compositor,'xrandr_activate_vkms',return_value=(True,{},'ready')) as create:
                    compositor.activate_in_compositor(desktop,[],1920,1080,60,'Virtual-1',41)
                    create.assert_called_once_with([],1920,1080,60,'Virtual-1',41)
                with patch.object(compositor,'xrandr_deactivate_vkms',return_value=True) as remove:
                    compositor.deactivate_in_compositor(desktop,[],target_name='Virtual-1',target_connector_id=41)
                    remove.assert_called_once_with({'Virtual-1':41})

    def test_xwayland_cannot_authorize_generic_x11_creation(self):
        with self.assertRaises(helper.VkmsHelperError):
            helper._validate_compositor_support('x11',{'XDG_SESSION_TYPE':'wayland','DISPLAY':':0'})
        with patch.dict(os.environ,{'XDG_SESSION_TYPE':'wayland','DISPLAY':':0','WAYLAND_DISPLAY':'wayland-0'},clear=True):
            self.assertIsNone(compositor.detect_compositor())

    def test_kde_and_gnome_wayland_dispatch_remains_unchanged(self):
        with patch.dict(os.environ,{'XDG_SESSION_TYPE':'wayland','WAYLAND_DISPLAY':'wayland-0'},clear=True):
            for desktop,method in [('kde','kde_activate_vkms'),('gnome','gnome_activate_vkms')]:
                with self.subTest(desktop=desktop),patch.object(compositor,method,return_value=(True,{},'ready')) as activate:
                    compositor.activate_in_compositor(desktop,[],1920,1080,60,'Virtual-1',41)
                    activate.assert_called_once()
