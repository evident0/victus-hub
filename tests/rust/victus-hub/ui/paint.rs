use super::*;

#[test]
fn dragging_one_point_cannot_raise_its_neighbors() {
    let mut points = vec![FanPoint { temp: 30, speed: 20 }, FanPoint { temp: 60, speed: 40 }, FanPoint { temp: 100, speed: 80 }];
    move_point(&mut points, 1, 70, 95);
    assert_eq!(points[1], FanPoint { temp: 70, speed: 80 });
    assert_eq!(points[2], FanPoint { temp: 100, speed: 80 });
    assert!(insert_point(&mut points, 70, 20).is_none());
    let index = insert_point(&mut points, 50, 100).unwrap();
    assert_eq!(points[index].speed, 50);
}
