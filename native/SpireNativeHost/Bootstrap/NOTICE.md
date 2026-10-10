GameBootstrap.cs, OfflineLocalization.cs, MainLoopContext.cs and MemorySaveStore.cs derive from
Torch1230/CombatSolver commit d7b46203593beb51f792628a2587b04f0850d1c2
tools/OfflineSearchHarness, under the included MIT license.

Local change: remove the CombatSolver display-server override, so this data host
depends only on the installed game, GodotSharp and Harmony. These bootstrap
bypasses are version-specific and are reported in every export. They are not
a guarantee of native Godot gameplay equivalence. No game source or DLL is
distributed in this directory.
