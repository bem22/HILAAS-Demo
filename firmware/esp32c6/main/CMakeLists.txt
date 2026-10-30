idf_component_register(SRCS "main.c" "../../common/selftest.c" INCLUDE_DIRS "." "../../common" "$ENV{HILAAS_IDENTITY_DIR}" REQUIRES esp_hw_support freertos)
if(NOT EXISTS "$ENV{HILAAS_IDENTITY_DIR}/build_identity.h")
    message(FATAL_ERROR "Build identity is missing; use the Workshop build action")
endif()
