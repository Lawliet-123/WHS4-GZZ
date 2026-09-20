cmake_minimum_required(VERSION 3.21)
project(ac_whistle CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 공격측에서 이미 덤프해둔 SDK 를 그대로 쓴다. 같은 4.0.2 빌드다.
set(SDK_DIR "" CACHE PATH "Dumper-7 CppSDK 루트 (SDK/Basic.hpp 가 있는 폴더)")

if(NOT EXISTS "${SDK_DIR}/SDK/Basic.hpp")
    message(FATAL_ERROR
        "CppSDK not found at ${SDK_DIR}. "
        "tools/whistle/CppSDK 가 있는지 확인하세요.")
endif()

set(SDK_SOURCES
    "${SDK_DIR}/SDK/Basic.cpp"
    "${SDK_DIR}/SDK/CoreUObject_functions.cpp"
    # Basic.cpp 의 GetStaticName() 이 UKismetStringLibrary::Conv_StringToName 을
    # 부른다. 이 파일을 빼면 LNK2019 가 난다.
    "${SDK_DIR}/SDK/Engine_functions.cpp"
)

add_library(ac_whistle SHARED src/main.cpp ${SDK_SOURCES})
target_include_directories(ac_whistle PRIVATE "${SDK_DIR}")

if(MSVC)
    # /utf-8  : 소스에 한글 주석이 있다. 없으면 cp949 로 읽어 C2001 이 난다.
    # /EHa    : __try/__except 를 C++ 예외와 섞으려면 필요하다.
    # /bigobj : SDK 헤더가 커서 오브젝트 섹션 상한에 걸린다.
    target_compile_options(ac_whistle PRIVATE /utf-8 /EHa /bigobj /permissive- /W3)
    target_compile_definitions(ac_whistle PRIVATE
        WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS)
endif()

# 주입된 DLL 은 매핑된 채로 남아 링커가 덮어쓰지 못한다(LNK1104).
# 코드를 고칠 때마다 이 숫자를 올려 새 파일명을 만든다.
set(AC_VERSION "6")

set_target_properties(ac_whistle PROPERTIES
    OUTPUT_NAME "ac_whistle_v${AC_VERSION}"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin")
