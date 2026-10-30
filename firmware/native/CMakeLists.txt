cmake_minimum_required(VERSION 3.16)
project(hilaas_native_selftest LANGUAGES C)

add_executable(hilaas-selftest main.c ../common/selftest.c)
target_include_directories(hilaas-selftest PRIVATE ../common)
foreach(identity COMMIT RUN_ID NONCE)
    if(DEFINED HILAAS_BUILD_${identity})
        target_compile_definitions(hilaas-selftest PRIVATE HILAAS_BUILD_${identity}="${HILAAS_BUILD_${identity}}")
    endif()
endforeach()
set_target_properties(hilaas-selftest PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED YES C_EXTENSIONS NO)
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(hilaas-selftest PRIVATE -Wall -Wextra -Werror -pedantic)
endif()

enable_testing()
add_test(NAME portable-selftest COMMAND hilaas-selftest)
set_tests_properties(portable-selftest PROPERTIES
    FAIL_REGULAR_EXPRESSION "status=fail")