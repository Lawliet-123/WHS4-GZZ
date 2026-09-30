@echo off
setlocal
call "%KS_VALIDATION_VSDEV%" -no_logo -arch=x64 -host_arch=x64
if errorlevel 1 exit /b 1
pushd "%~dp0.."
if not exist bin\obj mkdir bin\obj
cl /nologo /std:c11 /W3 /LD validation\fixture.c /Fobin\obj\fixture.obj /Febin\KsValidationFixture.dll /link /IMPLIB:bin\obj\fixture.lib
if errorlevel 1 goto failed
for %%T in (policy_test cache_test thread_layout_test thread_scan_test) do (
    cl /nologo /std:c11 /W3 /D_CRT_SECURE_NO_WARNINGS /IKernelSentinel\driver KernelSentinel\tests\%%T.c /Fobin\obj\%%T.obj /Febin\%%T.exe
    if errorlevel 1 goto failed
)
cl /nologo /std:c11 /W3 /D_CRT_SECURE_NO_WARNINGS /Ivalidation\native\stubs /IKernelSentinel\driver validation\native\native_sensor_test.c /Fobin\obj\native_sensor_test.obj /Febin\native_sensor_test.exe /link bcrypt.lib /NODEFAULTLIB:Aux_Klib.lib /NODEFAULTLIB:Cng.lib
if errorlevel 1 goto failed
popd
exit /b 0
:failed
popd
exit /b 1
