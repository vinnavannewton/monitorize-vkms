#include <linux/module.h>
#include <linux/export-internal.h>
#include <linux/compiler.h>

MODULE_INFO(name, KBUILD_MODNAME);

__visible struct module __this_module
__section(".gnu.linkonce.this_module") = {
	.name = KBUILD_MODNAME,
	.init = init_module,
#ifdef CONFIG_MODULE_UNLOAD
	.exit = cleanup_module,
#endif
	.arch = MODULE_ARCH_INIT,
};

KSYMTAB_FUNC(argb_u16_from_yuv161616, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(argb_u16_from_yuv161616, 0x00);
KSYMTAB_FUNC(get_conversion_matrix_to_argb_u16, "");
SYMBOL_FLAGS(get_conversion_matrix_to_argb_u16, 0x00);
KSYMTAB_FUNC(lerp_u16, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(lerp_u16, 0x00);
KSYMTAB_FUNC(get_lut_index, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(get_lut_index, 0x00);
KSYMTAB_FUNC(apply_lut_to_channel_value, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(apply_lut_to_channel_value, 0x00);
KSYMTAB_FUNC(apply_3x4_matrix, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(apply_3x4_matrix, 0x00);
KSYMTAB_FUNC(vkms_config_create, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_create, 0x00);
KSYMTAB_FUNC(vkms_config_default_create, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_default_create, 0x00);
KSYMTAB_FUNC(vkms_config_destroy, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_destroy, 0x00);
KSYMTAB_FUNC(vkms_config_valid_plane_rotation, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_valid_plane_rotation, 0x00);
KSYMTAB_FUNC(vkms_config_valid_plane_color_encoding, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_valid_plane_color_encoding, 0x00);
KSYMTAB_FUNC(vkms_config_valid_plane_color_range, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_valid_plane_color_range, 0x00);
KSYMTAB_FUNC(vkms_config_is_valid, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_is_valid, 0x00);
KSYMTAB_FUNC(vkms_config_create_plane, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_create_plane, 0x00);
KSYMTAB_FUNC(vkms_config_destroy_plane, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_destroy_plane, 0x00);
KSYMTAB_FUNC(vkms_config_plane_attach_crtc, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_plane_attach_crtc, 0x00);
KSYMTAB_FUNC(vkms_config_plane_detach_crtc, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_plane_detach_crtc, 0x00);
KSYMTAB_FUNC(vkms_config_create_crtc, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_create_crtc, 0x00);
KSYMTAB_FUNC(vkms_config_destroy_crtc, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_destroy_crtc, 0x00);
KSYMTAB_FUNC(vkms_config_crtc_primary_plane, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_crtc_primary_plane, 0x00);
KSYMTAB_FUNC(vkms_config_crtc_cursor_plane, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_crtc_cursor_plane, 0x00);
KSYMTAB_FUNC(vkms_config_create_encoder, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_create_encoder, 0x00);
KSYMTAB_FUNC(vkms_config_destroy_encoder, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_destroy_encoder, 0x00);
KSYMTAB_FUNC(vkms_config_encoder_attach_crtc, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_encoder_attach_crtc, 0x00);
KSYMTAB_FUNC(vkms_config_encoder_detach_crtc, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_encoder_detach_crtc, 0x00);
KSYMTAB_FUNC(vkms_config_create_connector, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_create_connector, 0x00);
KSYMTAB_FUNC(vkms_config_destroy_connector, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_destroy_connector, 0x00);
KSYMTAB_FUNC(vkms_config_connector_attach_encoder, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_connector_attach_encoder, 0x00);
KSYMTAB_FUNC(vkms_config_connector_detach_encoder, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_config_connector_detach_encoder, 0x00);
KSYMTAB_FUNC(vkms_configfs_parse_next_format, "EXPORTED_FOR_KUNIT_TESTING");
SYMBOL_FLAGS(vkms_configfs_parse_next_format, 0x00);
KSYMTAB_DATA(linear_eotf, "");
SYMBOL_FLAGS(linear_eotf, 0x00);
KSYMTAB_DATA(srgb_eotf, "");
SYMBOL_FLAGS(srgb_eotf, 0x00);
KSYMTAB_DATA(srgb_inv_eotf, "");
SYMBOL_FLAGS(srgb_inv_eotf, 0x00);

MODULE_INFO(depends, "");

