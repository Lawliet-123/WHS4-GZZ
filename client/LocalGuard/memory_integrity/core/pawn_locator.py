class PawnLocator:
    """
    MECCHA CHAMELEON 4.0.2
    Local player Pawn locator.

    This locator does not use telemetry.

    Memory path:

        PenguinHotel-Win64-Shipping.exe
            |
            + GWorld RVA
            |
            v
        UWorld
            |
            + World.OwningGameInstance
            |
            v
        GameInstance
            |
            + GameInstance.LocalPlayers
            |
            v
        LocalPlayers TArray
            |
            v
        LocalPlayer
            |
            + Player.PlayerController
            |
            v
        PlayerController
            |
            + PlayerController.AcknowledgedPawn
            |
            v
        Local Pawn

    All reads are read-only through ProcessMemory.
    """

    # ============================================================
    # MECCHA CHAMELEON 4.0.2 offsets
    # ============================================================

    GWORLD_RVA = 0x9613260

    OFFSET_WORLD_OWNING_GAME_INSTANCE = 0x228
    OFFSET_GAME_INSTANCE_LOCAL_PLAYERS = 0x38

    OFFSET_PLAYER_PLAYER_CONTROLLER = 0x30

    OFFSET_PLAYER_CONTROLLER_ACKNOWLEDGED_PAWN = 0x350

    # ============================================================
    # Pawn validation offsets
    # ============================================================

    OFFSET_DEAD = 0x05AA
    OFFSET_INVINCIBLE = 0x05AB

    OFFSET_HEALTH = 0x0638
    OFFSET_MAX_HEALTH = 0x0640
    OFFSET_CHANGE_BEFORE_HEALTH = 0x0648

    OFFSET_IS_HUNTER = 0x0C3A

    def __init__(self, memory):
        self.memory = memory

    # ============================================================
    # Helpers
    # ============================================================

    def _read_pointer(
        self,
        address,
    ):
        try:
            value = self.memory.read_pointer(
                address
            )

        except (
            OSError,
            RuntimeError,
        ):
            return 0

        if not value:
            return 0

        return value

    def _read_int32(
        self,
        address,
    ):
        try:
            return self.memory.read_int32(
                address
            )

        except (
            OSError,
            RuntimeError,
        ):
            return 0

    # ============================================================
    # Memory chain
    # ============================================================

    def get_world(self):
        """
        Resolve UWorld from the game's module base.

        GWorld is a global pointer stored at:

            module_base + GWORLD_RVA
        """

        if not self.memory.module_base:
            return 0

        gworld_address = (
            self.memory.module_base
            + self.GWORLD_RVA
        )

        return self._read_pointer(
            gworld_address
        )

    def get_game_instance(
        self,
        world=None,
    ):
        world = (
            world
            or self.get_world()
        )

        if not world:
            return 0

        return self._read_pointer(
            world
            + self.OFFSET_WORLD_OWNING_GAME_INSTANCE
        )

    def get_local_player(
        self,
        game_instance=None,
    ):
        game_instance = (
            game_instance
            or self.get_game_instance()
        )

        if not game_instance:
            return 0

        local_players_address = (
            game_instance
            + self.OFFSET_GAME_INSTANCE_LOCAL_PLAYERS
        )

        # Unreal TArray:
        #
        # +0x00 = Data pointer
        # +0x08 = Num
        # +0x0C = Max

        local_players_data = (
            self._read_pointer(
                local_players_address
            )
        )

        if not local_players_data:
            return 0

        local_players_count = (
            self._read_int32(
                local_players_address
                + 0x08
            )
        )

        if (
            local_players_count
            <= 0
            or local_players_count
            > 16
        ):
            return 0

        return self._read_pointer(
            local_players_data
        )

    def get_controller(
        self,
        local_player=None,
    ):
        local_player = (
            local_player
            or self.get_local_player()
        )

        if not local_player:
            return 0

        return self._read_pointer(
            local_player
            + self.OFFSET_PLAYER_PLAYER_CONTROLLER
        )

    def get_pawn(
        self,
        controller=None,
    ):
        controller = (
            controller
            or self.get_controller()
        )

        if not controller:
            return 0

        return self._read_pointer(
            controller
            + self.OFFSET_PLAYER_CONTROLLER_ACKNOWLEDGED_PAWN
        )

    # ============================================================
    # Pawn validation
    # ============================================================

    def validate_pawn(
        self,
        pawn_address,
    ):
        """
        Perform lightweight validation using fields already
        verified for MECCHA 4.0.2.

        This does not determine whether GodMode is active.
        It only checks whether the resolved pointer looks like
        the expected local Pawn object.
        """

        if not pawn_address:
            return False

        try:
            dead_raw = (
                self.memory.read_uint8(
                    pawn_address
                    + self.OFFSET_DEAD
                )
            )

            invincible_raw = (
                self.memory.read_uint8(
                    pawn_address
                    + self.OFFSET_INVINCIBLE
                )
            )

            is_hunter_raw = (
                self.memory.read_uint8(
                    pawn_address
                    + self.OFFSET_IS_HUNTER
                )
            )

            health = (
                self.memory.read_double(
                    pawn_address
                    + self.OFFSET_HEALTH
                )
            )

            max_health = (
                self.memory.read_double(
                    pawn_address
                    + self.OFFSET_MAX_HEALTH
                )
            )

            change_before_health = (
                self.memory.read_double(
                    pawn_address
                    + self.OFFSET_CHANGE_BEFORE_HEALTH
                )
            )

        except (
            OSError,
            RuntimeError,
        ):
            return False

        # UE bool-like fields should be byte-sized flags.
        if dead_raw not in (
            0,
            1,
        ):
            return False

        if invincible_raw not in (
            0,
            1,
        ):
            return False

        if is_hunter_raw not in (
            0,
            1,
        ):
            return False

        # Basic sanity checks.
        #
        # These are deliberately broad so the locator does not
        # reject legitimate gameplay states such as temporary
        # damage, healing, or death transitions.

        if not (
            -100000.0
            < health
            < 100000.0
        ):
            return False

        if not (
            0.0
            < max_health
            < 100000.0
        ):
            return False

        if not (
            -100000.0
            < change_before_health
            < 100000.0
        ):
            return False

        return True

    # ============================================================
    # Public API
    # ============================================================

    def locate(self):
        """
        Resolve and validate the current local Pawn.

        Returns:
            Pawn address as int when successful.
            None when the Pawn is not available yet.
        """

        pawn_address = (
            self.get_pawn()
        )

        if not pawn_address:
            return None

        if not self.validate_pawn(
            pawn_address
        ):
            return None

        return pawn_address

    def diagnostic(self):
        """
        Return the whole pointer chain for debugging.

        This is useful when one stage of the chain becomes
        invalid after a game update.
        """

        world = self.get_world()

        game_instance = (
            self.get_game_instance(
                world
            )
            if world
            else 0
        )

        local_player = (
            self.get_local_player(
                game_instance
            )
            if game_instance
            else 0
        )

        controller = (
            self.get_controller(
                local_player
            )
            if local_player
            else 0
        )

        pawn = (
            self.get_pawn(
                controller
            )
            if controller
            else 0
        )

        valid = (
            self.validate_pawn(
                pawn
            )
            if pawn
            else False
        )

        return {
            "world": world,
            "game_instance": game_instance,
            "local_player": local_player,
            "controller": controller,
            "pawn": pawn,
            "valid": valid,
        }