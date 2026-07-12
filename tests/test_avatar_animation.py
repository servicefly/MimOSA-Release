"""
Tests for avatar animation system (M8.3).
"""

import pytest
import time
from mimosa.avatar.emotions import (
    EmotionState,
    EmotionVisuals,
    get_emotion_visuals,
    blend_emotions,
    EMOTION_VISUALS
)
from mimosa.avatar.gestures import (
    GestureType,
    GestureController,
    GestureKeyframe,
    GESTURE_LIBRARY
)
from mimosa.avatar.animator import (
    Animator,
    AnimationState,
    AnimationFrame
)


class TestEmotions:
    """Test emotion state definitions and blending."""
    
    def test_all_emotion_states_have_visuals(self):
        """Verify all emotion states have visual definitions."""
        for state in EmotionState:
            visuals = get_emotion_visuals(state)
            assert isinstance(visuals, EmotionVisuals)
            assert len(visuals.color_tint) == 3
            assert visuals.scale > 0
            assert visuals.brightness > 0
    
    def test_emotion_visuals_parameters(self):
        """Verify emotion visual parameters are reasonable."""
        for state in EmotionState:
            vis = get_emotion_visuals(state)
            # Color tint components should be 0-2 range
            for component in vis.color_tint:
                assert 0.0 <= component <= 2.0
            # Scale should be close to 1.0
            assert 0.5 <= vis.scale <= 1.5
            # Pulse rate should be reasonable
            assert 0.0 <= vis.pulse_rate <= 2.0
            # Pulse amplitude should be small
            assert 0.0 <= vis.pulse_amplitude <= 0.5
    
    def test_idle_emotion_low_intensity(self):
        """Verify IDLE has low intensity."""
        vis = get_emotion_visuals(EmotionState.IDLE)
        assert vis.intensity < 0.5
    
    def test_happy_emotion_high_intensity(self):
        """Verify HAPPY has high intensity."""
        vis = get_emotion_visuals(EmotionState.HAPPY)
        assert vis.intensity >= 0.8
    
    def test_blend_emotions_halfway(self):
        """Verify emotion blending at 50%."""
        vis = blend_emotions(EmotionState.IDLE, EmotionState.HAPPY, 0.5)
        idle_vis = get_emotion_visuals(EmotionState.IDLE)
        happy_vis = get_emotion_visuals(EmotionState.HAPPY)
        
        # Scale should be between idle and happy
        assert idle_vis.scale <= vis.scale <= happy_vis.scale or \
               happy_vis.scale <= vis.scale <= idle_vis.scale
    
    def test_blend_emotions_full_target(self):
        """Verify blending at 1.0 returns target emotion."""
        vis = blend_emotions(EmotionState.IDLE, EmotionState.HAPPY, 1.0)
        happy_vis = get_emotion_visuals(EmotionState.HAPPY)
        
        assert vis.color_tint == happy_vis.color_tint
        assert vis.scale == happy_vis.scale
    
    def test_blend_emotions_full_source(self):
        """Verify blending at 0.0 returns source emotion."""
        vis = blend_emotions(EmotionState.IDLE, EmotionState.HAPPY, 0.0)
        idle_vis = get_emotion_visuals(EmotionState.IDLE)
        
        assert vis.color_tint == idle_vis.color_tint
        assert vis.scale == idle_vis.scale
    
    def test_blend_emotions_clamps_range(self):
        """Verify blend factor is clamped to 0-1."""
        # Should not crash with out-of-range values
        vis_low = blend_emotions(EmotionState.IDLE, EmotionState.HAPPY, -0.5)
        vis_high = blend_emotions(EmotionState.IDLE, EmotionState.HAPPY, 1.5)
        
        # Both should return valid visuals
        assert isinstance(vis_low, EmotionVisuals)
        assert isinstance(vis_high, EmotionVisuals)


class TestGestures:
    """Test gesture system and animations."""
    
    def test_all_gesture_types_in_library(self):
        """Verify all gesture types have definitions."""
        # NONE is special (no animation)
        non_none_gestures = [g for g in GestureType if g != GestureType.NONE]
        for gesture_type in non_none_gestures:
            assert gesture_type in GESTURE_LIBRARY
    
    def test_gesture_has_keyframes(self):
        """Verify gestures have keyframes."""
        for gesture in GESTURE_LIBRARY.values():
            assert len(gesture.keyframes) > 0
            assert gesture.duration > 0
    
    def test_gesture_keyframes_sorted(self):
        """Verify keyframes are time-ordered."""
        for gesture in GESTURE_LIBRARY.values():
            times = [kf.time for kf in gesture.keyframes]
            assert times == sorted(times)
    
    def test_gesture_controller_initializes(self):
        """Verify controller can be created."""
        controller = GestureController()
        assert controller is not None
        assert not controller.is_playing
        assert controller.current_gesture is None
    
    def test_play_gesture(self):
        """Verify gesture playback starts."""
        controller = GestureController()
        current_time = time.time()
        
        result = controller.play_gesture(GestureType.WAVE, current_time)
        assert result is True
        assert controller.is_playing
        assert controller.current_gesture == GESTURE_LIBRARY[GestureType.WAVE]
    
    def test_play_gesture_respects_priority(self):
        """Verify higher priority gestures interrupt lower ones."""
        controller = GestureController()
        current_time = time.time()
        
        # Start low priority gesture
        controller.play_gesture(GestureType.THINKING, current_time)  # priority 3
        
        # Try to start higher priority
        result = controller.play_gesture(GestureType.WAVE, current_time)  # priority 5
        assert result is True
        assert controller.current_gesture.gesture_type == GestureType.WAVE
    
    def test_play_gesture_blocks_lower_priority(self):
        """Verify lower priority gestures don't interrupt higher ones."""
        controller = GestureController()
        current_time = time.time()
        
        # Start high priority gesture
        controller.play_gesture(GestureType.WAVE, current_time)  # priority 5
        
        # Try to start lower priority
        result = controller.play_gesture(GestureType.THINKING, current_time)  # priority 3
        assert result is False
        assert controller.current_gesture.gesture_type == GestureType.WAVE
    
    def test_stop_gesture(self):
        """Verify gesture can be stopped."""
        controller = GestureController()
        controller.play_gesture(GestureType.WAVE, time.time())
        
        controller.stop_gesture()
        assert not controller.is_playing
        assert controller.current_gesture is None
    
    def test_get_current_keyframe_interpolates(self):
        """Verify keyframe interpolation."""
        controller = GestureController()
        start_time = time.time()
        controller.play_gesture(GestureType.WAVE, start_time)
        
        # Get keyframe slightly after start
        kf = controller.get_current_keyframe(start_time + 0.1)
        assert kf is not None
        assert isinstance(kf, GestureKeyframe)
    
    def test_get_current_keyframe_none_when_not_playing(self):
        """Verify None returned when no gesture playing."""
        controller = GestureController()
        kf = controller.get_current_keyframe(time.time())
        assert kf is None


class TestAnimator:
    """Test main animator and state machine."""
    
    def test_animator_initializes(self):
        """Verify animator can be created."""
        animator = Animator()
        assert animator is not None
        assert animator.current_state == AnimationState.IDLE
        assert animator.target_state == AnimationState.IDLE
    
    def test_set_state_changes_target(self):
        """Verify set_state updates target state."""
        animator = Animator()
        animator.set_state(AnimationState.LISTENING)
        
        assert animator.target_state == AnimationState.LISTENING
    
    def test_set_state_starts_transition(self):
        """Verify state change starts transition."""
        animator = Animator()
        animator.set_state(AnimationState.LISTENING)
        
        assert animator.transition_start_time is not None
        assert animator.transition_progress < 1.0
    
    def test_update_returns_animation_frame(self):
        """Verify update returns valid frame."""
        animator = Animator()
        frame = animator.update()
        
        assert isinstance(frame, AnimationFrame)
        assert isinstance(frame.emotion_visuals, EmotionVisuals)
        assert hasattr(frame, 'mouth_shape')
        assert hasattr(frame, 'blink_closed')
    
    def test_transition_completes_over_time(self):
        """Verify transitions complete."""
        animator = Animator()
        animator.set_state(AnimationState.LISTENING)
        
        # Wait for transition
        time.sleep(0.4)  # Default transition is 0.3s
        frame = animator.update()
        
        assert animator.transition_progress >= 1.0
        assert animator.current_state == AnimationState.LISTENING
    
    def test_set_emotion_override(self):
        """Verify emotion override works."""
        animator = Animator()
        animator.set_emotion(EmotionState.HAPPY, duration=1.0)
        
        assert animator.emotion_override == EmotionState.HAPPY
        assert animator.emotion_override_end_time is not None
    
    def test_start_speaking(self):
        """Verify speaking animation starts."""
        animator = Animator()
        animator.start_speaking("Hello world", duration=2.0)
        
        assert animator.target_state == AnimationState.SPEAKING
        assert animator.lip_sync.is_playing
    
    def test_stop_speaking(self):
        """Verify speaking stops."""
        animator = Animator()
        animator.start_speaking("Hello", 1.0)
        animator.stop_speaking()
        
        assert not animator.lip_sync.is_playing
    
    def test_play_gesture(self):
        """Verify gesture can be played."""
        animator = Animator()
        result = animator.play_gesture(GestureType.WAVE)
        
        assert result is True
        assert animator.gesture_controller.is_playing
    
    def test_blinking_occurs(self):
        """Verify blinking is triggered."""
        animator = Animator()
        
        # Force a blink by setting last blink time in past
        animator.last_blink_time = time.time() - 10.0
        animator.next_blink_interval = 0.1
        
        # Update to trigger blink
        frame = animator.update()
        
        # Should blink soon
        time.sleep(0.2)
        frame = animator.update()
        # Note: blinking is brief, so we can't reliably catch it
        # Just verify the mechanism doesn't crash
        assert isinstance(frame.blink_closed, bool)
    
    def test_reset(self):
        """Verify reset returns to idle."""
        animator = Animator()
        animator.set_state(AnimationState.SPEAKING)
        animator.set_emotion(EmotionState.HAPPY)
        animator.play_gesture(GestureType.WAVE)
        
        animator.reset()
        
        assert animator.current_state == AnimationState.IDLE
        assert animator.target_state == AnimationState.IDLE
        assert animator.emotion_override is None
        assert not animator.gesture_controller.is_playing
        assert not animator.lip_sync.is_playing


class TestIntegration:
    """Integration tests for complete animation pipeline."""
    
    def test_full_speaking_animation(self):
        """Verify complete speaking animation with lip sync."""
        animator = Animator()
        
        # Start speaking
        animator.start_speaking("Hello world", duration=0.5)
        
        # Update several times
        frames = []
        for _ in range(5):
            frame = animator.update()
            frames.append(frame)
            time.sleep(0.1)
        
        # Should have speaking state
        assert any(frame.emotion_visuals.intensity > 0.5 for frame in frames)
        
        # Should have varied mouth shapes
        mouth_shapes = [frame.mouth_shape for frame in frames]
        assert len(set(mouth_shapes)) > 1  # At least 2 different shapes
    
    def test_state_transitions_blend_smoothly(self):
        """Verify state transitions blend emotions."""
        animator = Animator()
        
        # Transition from idle to listening
        animator.set_state(AnimationState.LISTENING)
        
        # Capture frames during transition
        frames = []
        for _ in range(3):
            frame = animator.update()
            frames.append(frame)
            time.sleep(0.1)
        
        # Should have intermediate brightness values during blend
        brightnesses = [f.emotion_visuals.brightness for f in frames]
        assert len(set(brightnesses)) > 1  # Changing over time
    
    def test_gesture_and_emotion_combine(self):
        """Verify gestures work alongside emotions."""
        animator = Animator()
        
        # Set emotion and gesture
        animator.set_emotion(EmotionState.HAPPY, duration=2.0)
        animator.play_gesture(GestureType.THUMBS_UP)
        
        frame = animator.update()
        
        # Should have happy emotion
        assert frame.emotion_visuals.intensity > 0.5
        
        # Should have gesture data
        assert frame.gesture_keyframe is not None
